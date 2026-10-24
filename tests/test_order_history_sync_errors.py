import unittest

from utils.order_history_sync import (
    OrderHistorySyncError,
    classify_order_history_api_error,
)


class OrderHistorySyncErrorTests(unittest.TestCase):
    def test_classifies_session_expired_as_actionable_auth_error(self):
        error = classify_order_history_api_error(
            ["FAIL_SYS_SESSION_EXPIRED::Session过期"],
            cookie_id="acct-1",
        )

        self.assertIsInstance(error, OrderHistorySyncError)
        self.assertEqual(error.kind, "session_expired")
        self.assertIn("acct-1", str(error))
        self.assertIn("Cookie/Session 已过期", str(error))
        self.assertIn("手动刷新 Cookie", error.guidance)

    def test_classifies_permission_denied_as_account_permission_error(self):
        error = classify_order_history_api_error(
            ["PERMISSION_EXCEPTION::无权限访问"],
            cookie_id="acct-2",
        )

        self.assertEqual(error.kind, "permission_denied")
        self.assertIn("账号暂无订单列表访问权限", str(error))
        self.assertIn("卖家中心", error.guidance)

    def test_unknown_api_error_keeps_original_ret_value(self):
        error = classify_order_history_api_error(
            ["FAIL_SYS_UNKNOWN::unknown"],
            cookie_id="acct-3",
        )

        self.assertEqual(error.kind, "api_error")
        self.assertIn("FAIL_SYS_UNKNOWN::unknown", str(error))


if __name__ == "__main__":
    unittest.main()
