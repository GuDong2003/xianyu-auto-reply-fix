import asyncio
import unittest
from unittest import mock

from support import IsolatedDatabaseMixin
from utils.ai_pause import AIPauseStore, pause_after_manual_reply


class AIPauseTests(IsolatedDatabaseMixin,unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = AIPauseStore(self.db)
        with self.db.conn:
            self.db.conn.execute('DELETE FROM ai_reply_pauses')
            self.db.conn.execute('DELETE FROM ai_manual_pause_settings')
            self.db.conn.execute('DELETE FROM chat_messages')
        self.db.save_cookie('a','synthetic-cookie',1)
        self.db.save_cookie('b','synthetic-cookie',1)

    def test_pause_is_scoped_by_account_buyer_and_item(self):
        self.store.pause('a','buyer','item',10)
        self.assertGreater(self.store.remaining('a','buyer','item'),0)
        for key in (('b','buyer','item'),('a','other','item'),('a','buyer','other')):
            self.assertEqual(self.store.remaining(*key),0)

    def test_expiry_resume_and_restart(self):
        with mock.patch('utils.ai_pause.time.time',return_value=1000):
            self.store.pause('a','buyer','item',1)
            self.assertEqual(self.store.remaining('a','buyer','item'),60)
        with mock.patch('utils.ai_pause.time.time',return_value=1061):
            self.assertEqual(self.store.remaining('a','buyer','item'),0)
        self.store.pause('a','buyer','item',10)
        reopened = self.database_type(self.db.db_path)
        try:
            self.assertGreater(AIPauseStore(reopened).remaining('a','buyer','item'),0)
            AIPauseStore(reopened).resume('a','buyer','item')
            self.assertEqual(self.store.remaining('a','buyer','item'),0)
        finally:
            reopened.close()

    def test_manual_context_uses_only_incoming_messages_for_that_account(self):
        with self.db.conn:
            self.db.conn.execute("INSERT INTO chat_messages (cookie_id,chat_id,sender_id,item_id,direction) VALUES ('a','chat','buyer','item',2)")
            self.db.conn.execute("INSERT INTO chat_messages (cookie_id,chat_id,sender_id,item_id,direction) VALUES ('b','chat','foreign','foreign-item',2)")
        self.store.set_config('a',True,5)
        self.assertTrue(pause_after_manual_reply(self.db,'a','chat'))
        self.assertGreater(self.store.remaining('a','buyer','item'),0)
        self.assertEqual(self.store.remaining('a','foreign','foreign-item'),0)
        self.assertTrue(pause_after_manual_reply(self.db,'a','unknown-chat'))
        self.assertIsNone(self.store.context('a','unknown-chat'))

    def test_legacy_all_reply_pause_preserved_when_feature_disabled(self):
        from XianyuAutoAsync import AutoReplyPauseManager
        manager = AutoReplyPauseManager()
        manager.pause_chat = mock.Mock()
        with mock.patch('db_manager.db_manager',self.db):
            manager.pause_after_manual_reply('chat','a',buyer_id='buyer',item_id='item')
            manager.pause_chat.assert_called_once_with('chat','a')
            manager.pause_chat.reset_mock()
            self.store.set_config('a',True,10)
            manager.pause_after_manual_reply('chat','a',buyer_id='buyer',item_id='item')
            manager.pause_chat.assert_not_called()
        self.assertGreater(self.store.remaining('a','buyer','item'),0)

    def test_model_result_is_discarded_if_manual_reply_pauses_during_generation(self):
        import ai_reply_engine as module
        self.db.save_ai_reply_settings('a',{'ai_enabled':True,'custom_prompts':''})
        engine = module.AIReplyEngine()
        engine.save_conversation = mock.Mock(return_value='synthetic-time')
        engine._get_recent_user_messages = mock.Mock(return_value=[])
        engine.get_conversation_context = mock.Mock(return_value=[])
        engine.get_conversation_rounds = mock.Mock(return_value=0)
        def model(*args,**kwargs):
            self.store.pause('a','buyer','item',10)
            return 'AI result which must not be sent'
        engine._call_openai_chat_api = mock.Mock(side_effect=model)
        with mock.patch.object(module,'db_manager',self.db):
            result = engine.generate_reply('Hello',{'title':'Synthetic','desc':'Description'},'chat','a','buyer','item',skip_wait=True)
            self.assertIsNone(result)
            self.assertEqual(engine.save_conversation.call_count,1)
            engine._call_openai_chat_api.reset_mock()
            self.assertIsNone(engine.generate_reply('Hello',{},'chat','a','buyer','item',skip_wait=True))
            engine._call_openai_chat_api.assert_not_called()

    async def process_message(self, keyword=None, default=None, pause_after_generation=False):
        import XianyuAutoAsync as module
        live = module.XianyuLive.__new__(module.XianyuLive)
        live.cookie_id,live.myid = 'a','seller'
        live._safe_str = str
        live._check_buyer_blacklist_for_action = mock.Mock(return_value=None)
        live.get_item_specific_reply = mock.AsyncMock(return_value=None)
        live.get_keyword_reply = mock.AsyncMock(return_value=keyword)
        live.get_default_reply = mock.AsyncMock(return_value=default)
        live.get_ai_reply = mock.AsyncMock(return_value='AI reply')
        live.send_msg = mock.AsyncMock()
        calls = 0
        async def filters(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2 and pause_after_generation:
                self.store.pause('a','buyer','item',10)
            return {'matched':False}
        live._apply_message_filters = mock.AsyncMock(side_effect=filters)
        with mock.patch.object(module,'db_manager',self.db),mock.patch('db_manager.db_manager',self.db), \
             mock.patch.object(module,'pause_manager',module.AutoReplyPauseManager()),mock.patch('chat_event_hub.publish_chat_message'):
            await live._process_chat_message_reply({},'synthetic-ws','Buyer','buyer','Hello','item','chat','synthetic-time')
        return live

    async def test_keywords_and_defaults_continue_while_ai_is_paused(self):
        self.store.pause('a','buyer','item',10)
        for kwargs,text in (({'keyword':'Keyword reply'},'Keyword reply'),({'default':'Default reply'},'Default reply')):
            live = await self.process_message(**kwargs)
            live.send_msg.assert_awaited_once_with('synthetic-ws','chat','buyer',text)
            live.get_ai_reply.assert_not_awaited()

    async def test_ai_generation_skipped_when_paused(self):
        self.store.pause('a','buyer','item',10)
        live = await self.process_message()
        live.get_ai_reply.assert_not_awaited()
        live.send_msg.assert_not_awaited()

    async def test_pause_before_transport_blocks_already_generated_reply(self):
        live = await self.process_message(pause_after_generation=True)
        live.get_ai_reply.assert_awaited_once()
        live.send_msg.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
