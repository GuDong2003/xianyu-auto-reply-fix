import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from XianyuAutoAsync import XianyuLive


class AIReplyPreSendFilterTest(unittest.IsolatedAsyncioTestCase):
    def _make_live(self, ai_filter_result):
        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "seller-account"
        live.myid = "seller-user-id"
        live._check_buyer_blacklist_for_action = MagicMock(return_value=False)
        live._apply_message_filters = AsyncMock(side_effect=[
            {
                "matched": False,
                "skip_auto_reply": False,
                "skip_ai_reply": False,
                "rules": [],
            },
            ai_filter_result,
        ])
        live.get_item_specific_reply = AsyncMock(return_value=None)
        live.get_keyword_reply = AsyncMock(return_value=None)
        live.get_default_reply = AsyncMock(return_value=None)
        live.get_ai_reply = AsyncMock(return_value="稍等，我确认一下库存")
        live.send_notification = AsyncMock()
        live.send_msg = AsyncMock()
        return live

    async def _process(self, live):
        with (
            patch("XianyuAutoAsync.AUTO_REPLY", {"enabled": True}),
            patch("db_manager.db_manager.save_chat_message", return_value=101),
            patch("chat_event_hub.publish_chat_message"),
        ):
            await live._process_chat_message_reply(
                message_data={"headers": {}, "body": {}},
                websocket="ws",
                send_user_name="测试买家",
                send_user_id="buyer-123",
                send_message="还有库存吗？",
                item_id="item-456",
                chat_id="chat-789",
                msg_time="2026-08-20 20:00:00",
            )

    async def test_notify_only_rule_keeps_ai_reply(self):
        live = self._make_live({
            "matched": True,
            "skip_auto_reply": False,
            "skip_ai_reply": False,
            "notify_enabled": True,
            "rules": [{"id": 1, "name": "人工关注"}],
        })

        await self._process(live)

        self.assertEqual(live._apply_message_filters.await_count, 2)
        ai_filter_call = live._apply_message_filters.await_args_list[1]
        self.assertEqual(ai_filter_call.kwargs["message_source"], "ai")
        self.assertEqual(ai_filter_call.kwargs["send_message"], "稍等，我确认一下库存")
        live.send_msg.assert_awaited_once_with("ws", "chat-789", "buyer-123", "稍等，我确认一下库存")

    async def test_skip_rule_blocks_ai_reply(self):
        live = self._make_live({
            "matched": True,
            "skip_auto_reply": True,
            "skip_ai_reply": False,
            "notify_enabled": False,
            "rules": [{"id": 2, "name": "禁止发送"}],
        })

        await self._process(live)

        live.send_msg.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
