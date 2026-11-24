import asyncio
import json
import sys
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import AsyncMock

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


if __name__ == "__main__":
    unittest.main()
