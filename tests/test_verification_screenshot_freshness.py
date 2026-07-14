import os
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import reply_server
from utils.time_utils import parse_db_timestamp


class LatestRiskLogEpochUtcTest(unittest.TestCase):
    """风控日志时间戳必须按 UTC 解析，避免 Asia/Shanghai 部署 8 小时偏差。"""

    def test_utc_string_parsed_without_local_timezone_drift(self):
        # SQLite CURRENT_TIMESTAMP 写入的是 UTC 无时区字符串
        utc_str = "2026-07-13 09:52:05"
        expected = parse_db_timestamp(utc_str).timestamp()

        with mock.patch.object(
            reply_server.db_manager,
            "get_risk_control_logs",
            return_value=[{"updated_at": utc_str}],
        ):
            epoch = reply_server._get_latest_risk_log_epoch_for_account("acc")

        self.assertIsNotNone(epoch)
        self.assertAlmostEqual(epoch, expected, delta=0.001)
        # 对照"错误地按本地时区解析同一字符串"：东八区下同一时钟串按本地解释得到的
        # 绝对时刻比按 UTC 解释早 8 小时，故正确(UTC)结果应比它大 28800 秒。
        naive_local = __import__("datetime").datetime.strptime(
            utc_str, "%Y-%m-%d %H:%M:%S"
        ).timestamp()
        if naive_local != expected:  # 仅当运行环境本地时区非 UTC 时才有区分度
            self.assertAlmostEqual(expected - naive_local, 8 * 3600, delta=1)

    def test_picks_latest_among_multiple_and_skips_unparseable(self):
        with mock.patch.object(
            reply_server.db_manager,
            "get_risk_control_logs",
            return_value=[
                {"updated_at": "2026-07-13 10:00:00"},
                {"updated_at": ""},                    # 空 -> 跳过
                {"updated_at": "not-a-date"},          # 非法 -> 跳过
                {"created_at": "2026-07-13 11:30:00"}, # 回退 created_at
            ],
        ):
            epoch = reply_server._get_latest_risk_log_epoch_for_account("acc")
        self.assertAlmostEqual(
            epoch, parse_db_timestamp("2026-07-13 11:30:00").timestamp(), delta=0.001
        )

    def test_returns_none_when_no_logs(self):
        with mock.patch.object(
            reply_server.db_manager, "get_risk_control_logs", return_value=[]
        ):
            self.assertIsNone(reply_server._get_latest_risk_log_epoch_for_account("acc"))


class ScreenshotFreshnessTest(unittest.TestCase):
    """截图新鲜度纯函数：60 秒边界 + mtime 读取失败。"""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        self.tmp.write(b"x")
        self.tmp.close()
        self.path = self.tmp.name
        self.mtime = os.path.getmtime(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_no_risk_log_keeps_screenshot(self):
        self.assertEqual(
            reply_server._evaluate_screenshot_freshness(self.path, None),
            ("ok", None),
        )

    def test_boundary_just_within_gap_is_ok(self):
        # 风控恰好比截图晚 60 秒：不超过阈值，仍视为有效
        status, _ = reply_server._evaluate_screenshot_freshness(
            self.path, self.mtime + reply_server._SCREENSHOT_STALE_GAP_SECONDS
        )
        self.assertEqual(status, "ok")

    def test_boundary_just_over_gap_is_stale(self):
        # 超过 60 秒阈值 1 秒：判定过期
        status, message = reply_server._evaluate_screenshot_freshness(
            self.path, self.mtime + reply_server._SCREENSHOT_STALE_GAP_SECONDS + 1
        )
        self.assertEqual(status, "stale")
        self.assertTrue(message)

    def test_older_risk_keeps_screenshot(self):
        status, _ = reply_server._evaluate_screenshot_freshness(
            self.path, self.mtime - 3600
        )
        self.assertEqual(status, "ok")

    def test_deleted_file_race_reports_unavailable_not_stale(self):
        # 文件在选中后被并发删除：必须报"不可用"，不能因 mtime=0 误判为"过期"
        os.unlink(self.path)
        status, message = reply_server._evaluate_screenshot_freshness(
            self.path, self.mtime + 10 * 3600  # 即便风控远晚于截图，也不应报 stale
        )
        self.assertEqual(status, "unavailable")
        self.assertTrue(message)


if __name__ == "__main__":
    unittest.main()
