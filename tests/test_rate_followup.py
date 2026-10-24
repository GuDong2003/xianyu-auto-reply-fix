import asyncio
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from support import IsolatedDatabaseMixin
from utils.rate_followup import send_rate_thanks


class Connected(Enum):
    CONNECTED = 'connected'


class RateFollowupTests(IsolatedDatabaseMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner = self.db.get_user_by_username('admin')['id']
        self.db.save_cookie('a', 'synthetic-cookie', self.owner)
        self.db.set_rate_thanks_config('a', True, 'Synthetic thanks message')
        with self.db.conn:
            self.db.conn.execute('DELETE FROM rate_thanks_messages')
            self.db.conn.execute('DELETE FROM orders')
            self.db.conn.execute('''INSERT INTO orders
                (order_id,cookie_id,buyer_id,sid,is_rated) VALUES ('order','a','1234567890','9876543210',1)''')
        self.live = SimpleNamespace(cookie_id='a', user_id=self.owner, myid='9999999999',
                                    connection_state=Connected.CONNECTED, ws=object(), send_msg=mock.AsyncMock())
        module = SimpleNamespace(XianyuLive=mock.Mock(), ConnectionState=Connected)
        module.XianyuLive.get_instance.return_value = self.live
        self.enterContext(mock.patch.dict(sys.modules, {'XianyuAutoAsync': module}))

        async def run(cookie_id, factory):
            await factory()

        self.run_on_loop = self.enterContext(mock.patch(
            'utils.rate_followup._send_on_account_loop', new=mock.AsyncMock(side_effect=run),
        ))

    async def send(self, account='a'):
        return await send_rate_thanks(account, 'order', database=self.db)

    async def test_sends_once_and_records_persistent_state(self):
        self.assertEqual((await self.send())['status'], 'sent')
        self.assertEqual((await self.send())['status'], 'already_claimed')
        self.live.send_msg.assert_awaited_once_with(self.live.ws, '9876543210', '1234567890', 'Synthetic thanks message')
        self.assertEqual(self.db.get_rate_thanks_state('a', 'order'), 'sent')

    async def test_disabled_config_does_not_send(self):
        self.db.set_rate_thanks_config('a', False, 'Synthetic thanks message')
        self.assertEqual((await self.send())['status'], 'disabled')
        self.run_on_loop.assert_not_awaited()

    async def test_foreign_or_unrated_order_rejected(self):
        for sql in ("UPDATE orders SET cookie_id='another'", "UPDATE orders SET cookie_id='a',is_rated=0"):
            with self.db.conn:
                self.db.conn.execute(sql)
            self.assertEqual((await self.send())['status'], 'invalid_order')
        self.run_on_loop.assert_not_awaited()

    async def test_missing_recipient_and_wrong_runtime_owner_rejected(self):
        with self.db.conn:
            self.db.conn.execute("UPDATE orders SET sid='' WHERE order_id='order'")
        self.assertEqual((await self.send())['status'], 'missing_recipient')
        with self.db.conn:
            self.db.conn.execute("UPDATE orders SET sid='9876543210' WHERE order_id='order'")
        self.live.user_id = self.owner + 1
        self.assertEqual((await self.send())['status'], 'invalid_owner')
        self.run_on_loop.assert_not_awaited()

    async def test_blacklist_and_system_filter_block_message(self):
        with mock.patch.object(self.db, 'is_buyer_blacklisted', return_value={'scope': 'personal'}):
            self.assertEqual((await self.send())['status'], 'blocked')
        with mock.patch('message_filter_service.MessageFilterService') as filters:
            filters.return_value.match_rules.return_value = {'skip_auto_reply': True}
            self.assertEqual((await self.send())['status'], 'filtered')
        self.run_on_loop.assert_not_awaited()
        self.assertIsNone(self.db.get_rate_thanks_state('a', 'order'))

    async def test_send_failure_keeps_unknown_state_without_retry(self):
        self.run_on_loop.side_effect = TimeoutError()
        self.assertEqual((await self.send())['status'], 'unknown')
        self.assertEqual((await self.send())['status'], 'already_claimed')
        self.assertEqual(self.run_on_loop.await_count, 1)
        self.assertEqual(self.db.get_rate_thanks_state('a', 'order'), 'unknown')

    async def test_cancelled_send_remains_claimed(self):
        self.run_on_loop.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.send()
        self.assertEqual((await self.send())['status'], 'already_claimed')

    async def test_concurrent_senders_claim_only_one_message(self):
        results = await asyncio.gather(*(self.send() for _ in range(4)))
        self.assertEqual(sum(result['status'] == 'sent' for result in results), 1)
        self.live.send_msg.assert_awaited_once()

    async def test_rating_hook_runs_only_after_successful_rating(self):
        import auto_rate_task
        with self.db.conn:
            self.db.conn.execute("UPDATE orders SET is_rated=0 WHERE order_id='order'")
        service = SimpleNamespace(rate_buyer=mock.AsyncMock(return_value={'success': False, 'message': 'Synthetic failure'}))
        with mock.patch.object(auto_rate_task, 'db_manager', self.db), \
             mock.patch.object(auto_rate_task, 'RateService', return_value=service), \
             mock.patch.object(auto_rate_task, 'send_rate_thanks', new=mock.AsyncMock()) as thanks:
            self.assertFalse((await auto_rate_task.rate_order_once('a', 'order', 'Synthetic comment'))['success'])
            thanks.assert_not_awaited()
            service.rate_buyer.return_value = {'success': True, 'message': 'Synthetic success'}
            self.assertTrue((await auto_rate_task.rate_order_once('a', 'order', 'Synthetic comment'))['success'])
            thanks.assert_awaited_once_with('a', 'order')
            self.assertTrue(self.db.get_order_by_id('order')['is_rated'])

    def test_concurrent_database_claims_and_restart_do_not_duplicate(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(lambda _: self.db.claim_rate_thanks('a', 'order'), range(8)))
        self.assertEqual(sum(bool(value) for value in claims), 1)
        reopened = self.database_type(self.db.db_path)
        try:
            self.assertIsNone(reopened.claim_rate_thanks('a', 'order'))
            self.assertIsNone(reopened.claim_rate_thanks('other', 'order'))
        finally:
            reopened.close()


if __name__ == '__main__':
    unittest.main()
