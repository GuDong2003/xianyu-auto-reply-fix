import asyncio
import json
import sys
import unittest
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from XianyuAutoAsync import XianyuLive


class _FakeWebSocket:
    def __init__(self, messages):
        self._messages = list(messages)

    async def recv(self):
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(60)
        return ""


class TestWsTimeoutAndOrderLock(unittest.IsolatedAsyncioTestCase):
    def _make_live_stub(self):
        live = object.__new__(XianyuLive)
        live.cookie_id = "test_cookie"
        live._order_locks = defaultdict(lambda: asyncio.Lock())
        live._lock_usage_times = {}
        live.init = AsyncMock()
        live.create_chat = AsyncMock()
        live._safe_str = str
        return live

    async def test_await_create_chat_cid_aborts_immediately_on_reg_401(self):
        live = self._make_live_stub()
        reg_401 = json.dumps({
            "code": 401,
            "body": {"reason": "device id or appkey is not equal", "code": "4000001", "scope": "reg"},
        })
        ws = _FakeWebSocket([reg_401])

        cid = await asyncio.wait_for(
            live._await_create_chat_cid(ws, "buyer1", "item1", timeout=10.0),
            timeout=1.0,
        )
        self.assertIsNone(cid)

    async def test_await_create_chat_cid_times_out_when_server_silent(self):
        live = self._make_live_stub()
        ws = _FakeWebSocket([])

        cid = await asyncio.wait_for(
            live._await_create_chat_cid(ws, "buyer1", "item1", timeout=1.0),
            timeout=2.5,
        )
        self.assertIsNone(cid)

    async def test_await_create_chat_cid_extracts_cid(self):
        live = self._make_live_stub()
        ok_msg = json.dumps({"body": {"singleChatConversation": {"cid": "123456@goofish"}}})
        ws = _FakeWebSocket([ok_msg])

        cid = await live._await_create_chat_cid(ws, "buyer1", "item1", timeout=5.0)
        self.assertEqual(cid, "123456")

    async def test_acquire_order_lock_times_out_instead_of_deadlocking(self):
        live = self._make_live_stub()
        lock = live._order_locks["order_1"]
        await lock.acquire()
        try:
            with self.assertRaises(asyncio.TimeoutError):
                async with live._acquire_order_lock("order_1", timeout=1.0):
                    pass
        finally:
            lock.release()


    async def test_setup_and_create_chat_share_the_same_deadline(self):
        for phase in ("init", "create_chat"):
            with self.subTest(phase=phase):
                live = self._make_live_stub()
                ws = _FakeWebSocket([])
                ws.recv = AsyncMock()
                interrupted = asyncio.Event()

                async def stall(*args):
                    try:
                        await asyncio.Future()
                    finally:
                        interrupted.set()

                getattr(live, phase).side_effect = stall
                cid = await asyncio.wait_for(
                    live._await_create_chat_cid(ws, "buyer1", "item1", timeout=1.0),
                    timeout=2.5,
                )
                self.assertIsNone(cid)
                self.assertTrue(interrupted.is_set())
                ws.recv.assert_not_awaited()
                if phase == "init":
                    live.create_chat.assert_not_awaited()

    async def test_pre_send_timeout_releases_recovery_lock_without_sending(self):
        live = self._make_recovery_stub()
        ws = _FakeWebSocket([])

        async def stall_init(websocket):
            await asyncio.Future()

        live.init.side_effect = stall_init
        cid_lookup = live._await_create_chat_cid

        async def short_cid_lookup(websocket, toid, item_id, timeout):
            return await cid_lookup(websocket, toid, item_id, timeout=1.0)

        live._await_create_chat_cid = short_cid_lookup

        async def try_sending(*args, **kwargs):
            self.assertTrue(live._order_locks["order_1"].locked())
            return await live._handle_websocket_connection_steps(ws, "buyer1", "item1", ["payload"])

        # Substitute the delivery-rule and WebSocket-connect boundary, not the order lock or send handler.
        live._send_recovered_delivery_without_sid_locked = try_sending
        self.assertFalse(await asyncio.wait_for(
            live._send_recovered_delivery_without_sid(
                {}, order_id="order_1", item_id="item1", buyer_id="buyer1", source="test",
            ),
            timeout=2.5,
        ))
        live._send_delivery_steps.assert_not_awaited()
        self.assertFalse(live._order_locks["order_1"].locked())

    def _make_recovery_stub(self):
        live = self._make_live_stub()
        live.ws = object()
        live.is_auto_confirm_enabled = Mock(return_value=True)
        live._ensure_item_owned_by_current_account = AsyncMock(return_value=True)
        live._check_buyer_blacklist_for_action = Mock(return_value=False)
        live.can_auto_delivery = Mock(return_value=True)
        live.is_lock_held = Mock(return_value=False)
        live._record_delivery_log = Mock()
        live._send_delivery_steps = AsyncMock()
        return live

    async def test_sid_recovery_does_not_report_success_when_order_lock_times_out(self):
        live = self._make_recovery_stub()
        order = {"order_id": "order_1", "item_id": "item1", "buyer_id": "buyer1",
                 "order_status": "pending_ship", "sid": "chat1@goofish"}

        @asynccontextmanager
        async def unavailable_lock(*args, **kwargs):
            raise asyncio.TimeoutError
            yield  # pragma: no cover

        live._acquire_order_lock = unavailable_lock
        with patch('db_manager.db_manager._normalize_order_status', return_value="pending_ship"):
            self.assertIs(await live._auto_deliver_recovered_pending_order(order), False)
        live._send_delivery_steps.assert_not_awaited()
        live._record_delivery_log.assert_called_once()
        self.assertEqual(live._record_delivery_log.call_args.kwargs["status"], "failed")

    async def test_sid_recovery_reports_completion_even_if_success_notification_fails(self):
        live = self._make_recovery_stub()
        order = {"order_id": "order_1", "item_id": "item1", "buyer_id": "buyer1",
                 "order_status": "pending_ship", "sid": "chat1@goofish"}
        live._get_pending_delivery_finalization_meta = Mock(return_value={"delivery": "sent"})
        live._finalize_delivery_after_send = AsyncMock(return_value={"success": True})
        live._persist_delivery_finalization_state = Mock()
        live._sync_order_delivery_progress = Mock()
        live._activate_delivery_lock = Mock()
        live._release_data_reservation_if_needed = Mock()
        live.send_delivery_failure_notification = AsyncMock(side_effect=RuntimeError("offline"))

        with patch('db_manager.db_manager._normalize_order_status', return_value="pending_ship"):
            self.assertIs(await live._auto_deliver_recovered_pending_order(order), True)
        live._persist_delivery_finalization_state.assert_called_once()
        self.assertEqual(live._persist_delivery_finalization_state.call_args.kwargs["status"], "finalized")
        live._release_data_reservation_if_needed.assert_not_called()
        live._send_delivery_steps.assert_not_awaited()
        self.assertFalse(live._order_locks["order_1"].locked())


if __name__ == "__main__":
    unittest.main()
