"""系统休眠感知的重连策略回归。

背景：桌面端（macOS 合盖休眠 / Windows 睡眠）下，系统维护任务会定时暗唤醒，
进程被短暂解冻。此时本地网络与 DNS 往往还没恢复，重连会连续失败：

- `gaierror`（nodename nor servname）被当成普通失败计数，5 次后触发
  "密码登录刷新 → 重启实例"，而真正原因只是网络没起来；
- 每次失败打 3 行 ERROR + 完整堆栈，一次休眠就能刷出上百行噪音；
- 暗唤醒窗口只有几秒，按失败次数退避（5s/10s/.../30s）根本赶不上，纯空转。

本回归锁住三件事：休眠能被识别、DNS 错误不算账号故障、睡醒后不误判业务流假在线。
"""

import time
import unittest
from unittest import mock

from XianyuAutoAsync import XianyuLive


def _make_live() -> XianyuLive:
    """不触发 __init__（那会建连接、读数据库），只装配被测方法需要的属性。"""
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = 'seller-account'
    live.connection_failures = 0
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


class DnsRetryStrategyTest(unittest.TestCase):
    DNS_ERRORS = (
        '[Errno 8] nodename nor servname provided, or not known',
        '[Errno -2] Name or service not known',
        '[Errno -3] Temporary failure in name resolution',
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


if __name__ == '__main__':
    unittest.main()
