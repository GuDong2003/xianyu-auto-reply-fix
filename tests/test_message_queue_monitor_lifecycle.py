"""消息队列监控协程的生命周期回归。

背景：``_queue_stats_monitor`` 是由 ``while self.message_queue_running`` 驱动的常驻协程，
每 60 秒输出一次队列统计。它由 ``_start_message_queue_workers()`` 创建，但原先只做了一次
``_create_tracked_task(...)``，不保存引用；而 ``_stop_message_queue_workers()`` 只取消
``self.message_workers`` 里的 worker，从未取消监控协程。

WebSocket 每次重连都会在 ``finally`` 里调用 stop，重连成功后立刻再 start。两次调用之间
只隔几毫秒，而监控协程正卡在 ``sleep(60)``，醒来时 ``message_queue_running`` 已经被重新
置为 True，于是它会继续跑下去 —— 每重连一次就泄漏一个。

实测一个运行了一天的实例中，同一账号同时有近 20 个监控协程存活，
``消息队列统计`` 的单日日志量达到理论值的 20 倍。

本回归锁住：反复 start/stop 之后不残留监控协程。
"""

import asyncio
import unittest

from XianyuAutoAsync import XianyuLive


def _make_live() -> XianyuLive:
    """不触发 __init__（那会建连接、读数据库），只装配被测方法需要的属性。"""
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = 'seller-account'
    live.message_queue_enabled = True
    live.message_queue_running = False
    live.message_workers = []
    live._queue_stats_task = None
    live.background_tasks = set()
    live.message_queue_workers = 1
    live.message_queue_max_size = 100
    live.message_expire_seconds = 60
    live.message_queue = asyncio.PriorityQueue(maxsize=100)
    live.queue_stats = {
        'received': 0,
        'processed': 0,
        'dropped_full': 0,
        'dropped_expired': 0,
        'errors': 0,
        'last_stats_time': 0,
    }
    return live


class MessageQueueMonitorLifecycleTests(unittest.IsolatedAsyncioTestCase):

    async def test_reconnect_does_not_accumulate_monitors(self):
        """连续 5 次重连后不应残留任何未结束的协程。"""
        live = _make_live()

        for _ in range(5):
            await live._start_message_queue_workers()
            # 让监控协程真正跑进 await asyncio.sleep(60)：
            # 不切一次事件循环的话，协程还没开始执行就被 stop 了，测不出真实的泄漏时序
            await asyncio.sleep(0)
            await live._stop_message_queue_workers()

        await asyncio.sleep(0)
        pending = [task for task in live.background_tasks if not task.done()]
        self.assertEqual(
            [], pending,
            f'5 次重连后仍有 {len(pending)} 个协程未结束（监控协程泄漏）',
        )
        self.assertIsNone(live._queue_stats_task)

    async def test_stop_cancels_monitor_task(self):
        """stop 必须取消监控协程，而不是只把 running 置 False。"""
        live = _make_live()
        await live._start_message_queue_workers()
        await asyncio.sleep(0)

        monitor = live._queue_stats_task
        self.assertIsNotNone(monitor)
        self.assertFalse(monitor.done())

        await live._stop_message_queue_workers()
        self.assertTrue(monitor.done(), '监控协程没有被取消')
        self.assertIsNone(live._queue_stats_task)

    async def test_start_after_stop_keeps_single_monitor(self):
        """stop 之后再 start，只应存在一个新的监控协程。"""
        live = _make_live()
        await live._start_message_queue_workers()
        await asyncio.sleep(0)
        first = live._queue_stats_task

        await live._stop_message_queue_workers()
        await live._start_message_queue_workers()
        await asyncio.sleep(0)
        second = live._queue_stats_task

        self.assertIsNot(first, second)
        self.assertTrue(first.done())

        await live._stop_message_queue_workers()
        await asyncio.sleep(0)
        self.assertEqual([t for t in live.background_tasks if not t.done()], [])
