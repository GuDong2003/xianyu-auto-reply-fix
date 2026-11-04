"""系统休眠感知的重连策略回归。

背景：桌面端（macOS 合盖休眠 / Windows 睡眠）下，系统维护任务会定时暗唤醒，
进程被短暂解冻。此时本地网络与 DNS 往往还没恢复，重连会连续失败：

- `gaierror`（nodename nor servname）被当成普通失败计数，5 次后触发
  "密码登录刷新 → 重启实例"，而真正原因只是网络没起来；
- 每次失败打 3 行 ERROR + 完整堆栈，一次休眠就能刷出上百行噪音；
- 暗唤醒窗口只有几秒，按失败次数退避（5s/10s/.../30s）根本赶不上，纯空转。

本回归锁住三件事：休眠能被识别、DNS 错误不算账号故障、睡醒后不误判业务流假在线。
"""

import asyncio
import socket
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from XianyuAutoAsync import XianyuLive


def _make_live() -> XianyuLive:
    """不触发 __init__（那会建连接、读数据库），只装配被测方法需要的属性。"""
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = 'seller-account'
    live.connection_failures = 0
    return live


def _make_reconnect_live(errors) -> XianyuLive:
    live = _make_live()
    live.ws = None
    live.base_url = 'wss://example.invalid/ws'
    live.current_token = None
    live.max_connection_failures = 5
    live.background_tasks = set()
    for field in ('heartbeat_task', 'token_refresh_task', 'cleanup_task',
                  'cookie_refresh_task', 'stream_watchdog_task'):
        setattr(live, field, None)
    live.create_session = mock.AsyncMock()
    live.close_session = mock.AsyncMock()
    live._unregister_instance = mock.Mock()
    live._set_connection_state = mock.Mock()
    live.get_init_auth_failure_state = mock.Mock(return_value={})
    live._build_websocket_headers = mock.Mock(return_value={})
    live._reset_background_tasks = mock.Mock()
    live._try_password_login_refresh = mock.AsyncMock(return_value=False)
    live._restart_instance = mock.AsyncMock()
    live._create_websocket_connection = mock.AsyncMock(side_effect=[
        *errors, asyncio.CancelledError(),
    ])
    return live


class SleepGapDetectionTest(unittest.TestCase):
    def test_first_call_has_no_history(self):
        live = _make_live()
        self.assertEqual(live._check_sleep_gap(), 0.0)

    def test_normal_interval_is_not_sleep(self):
        live = _make_live()
        with mock.patch('XianyuAutoAsync.time.time', return_value=1000.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=500.0):
            live._check_sleep_gap()
        # 墙钟与单调钟同步前进 5 秒 → 没有休眠
        with mock.patch('XianyuAutoAsync.time.time', return_value=1005.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=505.0):
            self.assertEqual(live._check_sleep_gap(), 0.0)
        self.assertFalse(hasattr(live, '_last_resume_at'))

    def test_suspend_is_measured_by_clock_skew(self):
        live = _make_live()
        with mock.patch('XianyuAutoAsync.time.time', return_value=1000.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=500.0):
            live._check_sleep_gap()
        # 墙钟走了 1000 秒，单调钟只走了 10 秒 → 睡了约 990 秒
        with mock.patch('XianyuAutoAsync.time.time', return_value=2000.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=510.0):
            self.assertAlmostEqual(live._check_sleep_gap(), 990.0, delta=1)
        self.assertEqual(live._last_resume_at, 2000.0)

    def test_backwards_clock_never_returns_negative(self):
        live = _make_live()
        with mock.patch('XianyuAutoAsync.time.time', return_value=2000.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=500.0):
            live._check_sleep_gap()
        # NTP 回拨导致墙钟倒退，不能让调用方拿到负数
        with mock.patch('XianyuAutoAsync.time.time', return_value=1900.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=510.0):
            self.assertEqual(live._check_sleep_gap(), 0.0)

    def test_windows_resume_is_detected_from_missed_watchdog_deadline(self):
        live = _make_live()
        with mock.patch('XianyuAutoAsync.time.time', return_value=1000.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=500.0):
            live._check_sleep_gap()
        # Windows 的两种时钟都会计入休眠，检测看门狗原定的检查时间。
        with mock.patch('XianyuAutoAsync.time.time', return_value=2000.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=1500.0):
            self.assertGreaterEqual(
                live._check_sleep_gap(expected_check_at=530.0), 60,
            )
        self.assertEqual(live._last_resume_at, 2000.0)

    def test_reconnect_sample_does_not_hide_missed_watchdog_deadline(self):
        live = _make_live()
        with mock.patch('XianyuAutoAsync.time.time', return_value=1000.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=500.0):
            live._check_sleep_gap()
        with mock.patch('XianyuAutoAsync.time.time', return_value=2000.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=1500.0):
            live._check_sleep_gap()  # 主循环先更新了共享采样点。
            self.assertGreaterEqual(
                live._check_sleep_gap(expected_check_at=530.0), 60,
            )
        self.assertEqual(live._last_resume_at, 2000.0)

    def test_on_time_watchdog_does_not_mark_resume(self):
        live = _make_live()
        with mock.patch('XianyuAutoAsync.time.time', return_value=1000.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=500.0):
            live._check_sleep_gap()
        with mock.patch('XianyuAutoAsync.time.time', return_value=1030.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=530.0):
            self.assertEqual(live._check_sleep_gap(expected_check_at=530.0), 0)
        self.assertFalse(hasattr(live, '_last_resume_at'))


class DnsRetryStrategyTest(unittest.TestCase):
    DNS_ERRORS = (
        '[Errno 8] nodename nor servname provided, or not known',
        '[Errno -2] Name or service not known',
        '[Errno -3] Temporary failure in name resolution',
        '[Errno 11001] getaddrinfo failed',
    )

    def test_dns_errors_retry_immediately(self):
        live = _make_live()
        live.connection_failures = 4  # 即便连续失败很多次，DNS 错误也不该退避
        for msg in self.DNS_ERRORS:
            with self.subTest(msg=msg):
                self.assertEqual(live._calculate_retry_delay(msg), 3)

    def test_non_dns_errors_keep_backoff(self):
        live = _make_live()
        live.connection_failures = 2
        # 其他未知错误仍走 5 × failures（上限 30），确认没被误改
        self.assertEqual(live._calculate_retry_delay('boom'), 10)
        live.connection_failures = 20
        self.assertEqual(live._calculate_retry_delay('boom'), 30)

    def test_windows_dns_error_has_positive_delay_with_no_failures(self):
        live = _make_live()
        error = socket.gaierror(11001, 'getaddrinfo failed')
        self.assertEqual(live._calculate_retry_delay(error), 3)

    def test_wrapped_dns_error_uses_cause_instead_of_localized_message(self):
        live = _make_live()
        error = OSError('网络连接失败')
        error.__cause__ = socket.gaierror(11001, '无法解析主机')
        self.assertTrue(live._is_dns_error(error))
        self.assertEqual(live._calculate_retry_delay(error), 3)

    def test_dns_retry_preserves_account_pause(self):
        live = _make_live()
        with mock.patch.object(live, '_is_account_pause_status', return_value=True), \
                mock.patch.object(live, '_compute_token_retry_wait_seconds', return_value=900):
            self.assertEqual(
                live._calculate_retry_delay(socket.gaierror(11001, 'getaddrinfo failed')),
                900,
            )

    def test_warn_dedup_window(self):
        live = _make_live()
        with mock.patch('XianyuAutoAsync.time.time', return_value=1000.0):
            self.assertFalse(live._dns_warn_dedup())      # 第一次要打
        with mock.patch('XianyuAutoAsync.time.time', return_value=1300.0):
            self.assertTrue(live._dns_warn_dedup())       # 窗口内吞掉
        with mock.patch('XianyuAutoAsync.time.time', return_value=1601.0):
            self.assertFalse(live._dns_warn_dedup())      # 超过 600s 再打一次


class WatchdogSleepGuardTest(unittest.TestCase):
    """看门狗必须在"刚睡醒"时闭嘴，否则会把休眠当成业务流假在线。"""

    def test_sleep_is_detected_as_guard_condition(self):
        live = _make_live()
        with mock.patch('XianyuAutoAsync.time.time', return_value=1000.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=500.0):
            live._check_sleep_gap()
        with mock.patch('XianyuAutoAsync.time.time', return_value=1600.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=505.0):
            # 看门狗里的判据就是这个 >= 60
            self.assertGreaterEqual(live._check_sleep_gap(), 60)
        self.assertEqual(live._last_resume_at, 1600.0)

    def test_recent_resume_marks_grace(self):
        live = _make_live()
        with mock.patch('XianyuAutoAsync.time.time', return_value=1000.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=500.0):
            live._check_sleep_gap()
        with mock.patch('XianyuAutoAsync.time.time', return_value=1600.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=505.0):
            live._check_sleep_gap()
        # 醒来 300 秒内，看门狗按 _last_resume_at 跳过
        self.assertLess(1600.0 - live._last_resume_at, 300)
        self.assertGreater(time.time(), 0)


class ReconnectLoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_windows_dns_errors_wait_without_account_recovery(self):
        live = _make_reconnect_live([
            socket.gaierror(11001, 'getaddrinfo failed') for _ in range(6)
        ])

        with mock.patch('cookie_manager.manager', None), \
                mock.patch('XianyuAutoAsync.asyncio.sleep', new_callable=mock.AsyncMock) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await live.main()

        self.assertEqual(live.connection_failures, 0)
        self.assertEqual(sleep.await_args_list, [mock.call(3)] * 6)
        live._try_password_login_refresh.assert_not_awaited()
        live._restart_instance.assert_not_awaited()

    async def test_windows_resume_during_offline_retry_enables_slow_retry(self):
        live = _make_reconnect_live([
            socket.gaierror(11001, 'getaddrinfo failed') for _ in range(2)
        ])
        clock = {'wall': 1000.0, 'mono': 500.0}

        async def sleep_with_suspend(seconds):
            if clock['wall'] == 1000.0:
                clock.update(wall=2000.0, mono=1500.0)
            else:
                clock['wall'] += seconds
                clock['mono'] += seconds

        with mock.patch('cookie_manager.manager', None), \
                mock.patch('XianyuAutoAsync.time.time', side_effect=lambda: clock['wall']), \
                mock.patch('XianyuAutoAsync.time.monotonic', side_effect=lambda: clock['mono']), \
                mock.patch('XianyuAutoAsync.asyncio.sleep', side_effect=sleep_with_suspend) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await live.main()

        self.assertEqual(live._last_resume_at, 2000.0)
        self.assertEqual(sleep.await_args_list, [mock.call(3)] + [mock.call(5)] * 24)
        self.assertEqual(live.connection_failures, 0)
        live._try_password_login_refresh.assert_not_awaited()
        live._restart_instance.assert_not_awaited()

    async def test_first_watchdog_wait_handles_windows_resume(self):
        live = _make_live()
        live.heartbeat_timeout = 30
        live.heartbeat_interval = 15
        live.stream_watchdog_check_interval = 30
        live.ws = SimpleNamespace(closed=False)
        live.last_successful_connection = 1000.0
        live.stream_watchdog_grace_period = 30
        live.last_heartbeat_response = 2000.0
        live.last_non_heartbeat_message_time = 1000.0
        live.message_stream_watchdog_timeout = 120
        live.last_stream_watchdog_reconnect_time = 0
        live.last_sync_package_time = 0
        live.last_user_chat_time = 0
        live._force_websocket_reconnect = mock.AsyncMock()
        live._maybe_notify_message_stream_stale = mock.AsyncMock()
        clock = {'wall': 1000.0, 'mono': 500.0}

        async def resume_then_cancel(_duration):
            if clock['wall'] == 1000.0:
                clock.update(wall=2000.0, mono=1500.0)
                return
            raise asyncio.CancelledError()

        live._interruptible_sleep = mock.AsyncMock(side_effect=resume_then_cancel)
        with mock.patch('cookie_manager.manager', None), \
                mock.patch('XianyuAutoAsync.time.time', side_effect=lambda: clock['wall']), \
                mock.patch('XianyuAutoAsync.time.monotonic', side_effect=lambda: clock['mono']):
            with self.assertRaises(asyncio.CancelledError):
                await live.message_stream_watchdog_loop()

        self.assertEqual(live._last_resume_at, 2000.0)
        live._force_websocket_reconnect.assert_not_awaited()
        live._maybe_notify_message_stream_stale.assert_not_awaited()

    async def test_resume_dns_backoff_preserves_credential_wait(self):
        live = _make_reconnect_live([
            socket.gaierror(11001, 'getaddrinfo failed'),
        ])
        live.last_token_refresh_status = 'password_login_backoff_wait'
        live._last_resume_at = 1000.0
        live._compute_token_retry_wait_seconds = mock.Mock(return_value=900)
        with mock.patch('cookie_manager.manager', None), \
                mock.patch('XianyuAutoAsync.time.time', return_value=1000.0), \
                mock.patch('XianyuAutoAsync.time.monotonic', return_value=500.0), \
                mock.patch('XianyuAutoAsync.asyncio.sleep', new_callable=mock.AsyncMock) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await live.main()

        self.assertEqual(sum(call.args[0] for call in sleep.await_args_list), 900)
        live._try_password_login_refresh.assert_not_awaited()
        live._restart_instance.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
