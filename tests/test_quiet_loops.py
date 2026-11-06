"""常驻循环的等待方式与日志噪音回归。

背景：`_interruptible_sleep` 原来把每段 sleep 拆成 1 秒一片循环，注释说为了
"及时响应取消信号"。但 `asyncio.sleep` 本身就能被 `task.cancel()` 立刻打断，
分片纯属多余：

- 5 个常驻循环 × 每个账号，按各自间隔累计约 86.4 万次唤醒/天（单账号）；
- 每次唤醒约 0.146ms，原生 sleep 只要 0.03ms，差 5 倍。

同时锁住三处日志噪音：
- 心跳每 15 秒打一条 WARNING，一天上万行，真告警会被淹；
- Playwright 缓存清理用的是 Docker 路径，桌面端永远为空却照样打 WARNING；
- 队列统计每分钟一条，队列健康时零信息量。

这三条都不影响功能，但会让日志失去可读性 —— 排查真实故障时成本很高。
"""

import asyncio
import time
import unittest
from unittest import mock

from XianyuAutoAsync import XianyuLive


def _make_live() -> XianyuLive:
    """不触发 __init__（会建连接、读数据库），只装配被测方法需要的属性。"""
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = 'seller-account'
    return live


class TestInterruptibleSleep(unittest.IsolatedAsyncioTestCase):
    """等待本身的行为契约。"""

    async def test_sleeps_full_duration(self):
        live = _make_live()
        started = time.monotonic()
        await live._interruptible_sleep(2)
        self.assertGreaterEqual(time.monotonic() - started, 1.9)

    async def test_cancel_interrupts_immediately(self):
        """取消必须立刻生效 —— 这是移除 1 秒分片的前提。

        如果这条挂了，说明不能简化成原生 sleep（分片才有意义）。
        """
        live = _make_live()

        async def runner():
            await live._interruptible_sleep(600)

        task = asyncio.create_task(runner())
        await asyncio.sleep(0.2)
        started = time.monotonic()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertLess(time.monotonic() - started, 1.0)

    async def test_zero_and_negative_no_hang(self):
        """0 或负数不能卡住：调用方会传 max(...) 结果，但也可能算出 0。"""
        live = _make_live()
        started = time.monotonic()
        await live._interruptible_sleep(0)
        await live._interruptible_sleep(-1)
        self.assertLess(time.monotonic() - started, 1.0)

    async def test_does_not_wake_periodically(self):
        """睡 3 秒应该只产生 1 次 sleep 调用，而不是 3 次分片。"""
        live = _make_live()
        calls = []
        real_sleep = asyncio.sleep

        async def counting_sleep(delay, *args, **kwargs):
            calls.append(delay)
            return await real_sleep(delay, *args, **kwargs)

        with mock.patch('asyncio.sleep', counting_sleep):
            await live._interruptible_sleep(3)

        self.assertEqual(len(calls), 1, f'应只有 1 次 sleep 调用，实际 {calls}')
        self.assertAlmostEqual(float(calls[0]), 3.0, places=1)


class TestHeartbeatLogThrottle(unittest.IsolatedAsyncioTestCase):
    """心跳响应日志按窗口节流，但断线恢复后首条必须记。"""

    def _make_heartbeat_live(self):
        live = _make_live()
        live.pending_heartbeat_mids = __import__('collections').deque(maxlen=32)
        live.heartbeat_interval = 15
        live.heartbeat_timeout = 10
        live.last_heartbeat_response = 0
        live._last_hb_log_at = 0
        live._hb_was_stale = True
        return live

    def _response(self, mid, live):
        return {
            'code': 200,
            'headers': {'mid': mid, 'sid': 'x'},
        }

    async def _handle(self, live, mid, when):
        live.pending_heartbeat_mids.append(mid)
        with mock.patch('XianyuAutoAsync.time.time', return_value=when):
            return await live.handle_heartbeat_response(self._response(mid, live))

    async def test_first_response_after_connect_is_logged(self):
        live = self._make_heartbeat_live()
        with mock.patch('XianyuAutoAsync.logger') as logger_mock:
            ok = await self._handle(live, 'mid-1', 1_000_000.0)
        self.assertTrue(ok)
        self.assertEqual(logger_mock.warning.call_count, 1, '新连接首条心跳必须记日志')

    async def test_subsequent_responses_within_window_are_silent(self):
        live = self._make_heartbeat_live()
        with mock.patch('XianyuAutoAsync.logger') as logger_mock:
            await self._handle(live, 'mid-1', 1_000_000.0)
            logger_mock.warning.reset_mock()
            # 15 秒后、距上次记日志才 15 秒 → 应该被吞掉
            await self._handle(live, 'mid-2', 1_000_015.0)
            self.assertEqual(logger_mock.warning.call_count, 0, '窗口内的心跳不应再记')

    async def test_logs_again_after_window(self):
        live = self._make_heartbeat_live()
        with mock.patch('XianyuAutoAsync.logger') as logger_mock:
            await self._handle(live, 'mid-1', 1_000_000.0)
            logger_mock.warning.reset_mock()
            await self._handle(live, 'mid-2', 1_000_400.0)
            self.assertEqual(logger_mock.warning.call_count, 1, '超过窗口应重新记一条')

    async def test_recovery_is_logged_even_within_window(self):
        """断线恢复必须立刻记，否则"到底通没通"没法判断。"""
        live = self._make_heartbeat_live()
        with mock.patch('XianyuAutoAsync.logger') as logger_mock:
            await self._handle(live, 'mid-1', 1_000_000.0)
            logger_mock.warning.reset_mock()
            live._hb_was_stale = True
            await self._handle(live, 'mid-2', 1_000_005.0)
            self.assertEqual(logger_mock.warning.call_count, 1, '恢复后首条必须记')


class TestPlaywrightCacheLogSpam(unittest.TestCase):
    """桌面上路径永远不存在，不该每 5 分钟刷一条 WARNING。"""

    def test_empty_result_does_not_log_warning(self):
        import glob as glob_module

        live = _make_live()

        async def run():
            await live._cleanup_playwright_cache()

        # glob 在函数内部 import，所以patch 模块本体而不是 XianyuAutoAsync 的属性
        with mock.patch.object(glob_module, 'glob', return_value=[]), \
                mock.patch('XianyuAutoAsync.logger') as logger_mock:
            asyncio.run(run())

        self.assertEqual(
            logger_mock.warning.call_count, 0,
            '没有可清理的文件时不应产生 WARNING 日志',
        )


class TestQueueStatsLogThrottle(unittest.TestCase):
    """队列统计：异常必报，健康时降频。"""

    def test_source_has_throttle_branch(self):
        import inspect

        src = inspect.getsource(XianyuLive._queue_stats_monitor)
        self.assertIn('_last_queue_stats_log_at', src, '需要有节流时间戳')
        # 有丢弃/错误/积压时必须仍然打印
        for marker in ("stats['dropped_full']", "stats['errors']", 'qsize()'):
            self.assertIn(marker, src, f'{marker} 参与"必须打印"的判断')


if __name__ == '__main__':
    unittest.main(verbosity=2)
