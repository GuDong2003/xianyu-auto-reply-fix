import unittest
from unittest.mock import patch

from XianyuAutoAsync import AutoReplyPauseManager


class AutoReplyPauseAccountScopeTest(unittest.TestCase):
    def test_same_chat_id_is_isolated_between_accounts(self):
        manager = AutoReplyPauseManager()

        with patch("XianyuAutoAsync.time.time", return_value=1000):
            manager.pause_chat_for("shared-chat", "seller-1", 3)

            self.assertTrue(manager.is_chat_paused("shared-chat", "seller-1"))
            self.assertFalse(manager.is_chat_paused("shared-chat", "seller-2"))
            self.assertEqual(manager.get_remaining_pause_time("shared-chat", "seller-1"), 180)
            self.assertEqual(manager.get_remaining_pause_time("shared-chat", "seller-2"), 0)


if __name__ == "__main__":
    unittest.main()
