from contextlib import ExitStack
import ast
import importlib
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from types import MethodType
import unittest
from unittest import mock

from cryptography.fernet import Fernet
from loguru import logger

from utils.item_sync import ItemSyncInProgress, item_sync_slot, sync_all_item_pages


def page(ids, **extra):
    return {'success': True, 'items': [{'id': str(i)} for i in ids], 'saved_count': len(ids), **extra}


class ItemSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = mock.Mock()
        self.db.reconcile_item_listing.return_value = 2
        self.runtime = SimpleNamespace(cookie_id='synthetic-account', get_item_list_info=mock.AsyncMock())
        self.enterContext(mock.patch('utils.item_sync.asyncio.sleep', new=mock.AsyncMock()))

    async def sync(self, responses, **kwargs):
        self.runtime.get_item_list_info.side_effect = responses
        return await sync_all_item_pages(self.runtime, self.db, page_size=2, **kwargs)

    async def test_complete_snapshot_reconciles_only_after_last_page(self):
        result = await self.sync([page(['a', 'b']), page(['c'])])
        self.assertTrue(result['success'])
        self.assertTrue(result['complete'])
        self.assertEqual(result['total_count'], 3)
        self.assertEqual(result['total_pages'], 2)
        self.assertEqual(result['unlisted_count'], 2)
        self.db.reconcile_item_listing.assert_called_once()
        self.assertEqual(self.db.reconcile_item_listing.call_args.args[:2], ('synthetic-account', {'a', 'b', 'c'}))

    async def test_empty_complete_snapshot_can_mark_old_items_unlisted(self):
        self.assertTrue((await self.sync([page([])]))['success'])
        self.assertEqual(self.db.reconcile_item_listing.call_args.args[1], set())

    async def test_first_page_failure_is_failure_without_reconciliation(self):
        result = await self.sync([{'error': 'synthetic failure'}])
        self.assertFalse(result['success'])
        self.assertFalse(result['partial'])
        self.db.reconcile_item_listing.assert_not_called()

    async def test_later_failure_keeps_partial_count_but_does_not_reconcile(self):
        result = await self.sync([page(['a', 'b']), {'success': False, 'error': 'synthetic failure'}])
        self.assertFalse(result['success'])
        self.assertTrue(result['partial'])
        self.assertEqual(result['total_count'], 2)
        self.db.reconcile_item_listing.assert_not_called()

    async def test_maximum_pages_does_not_turn_partial_snapshot_into_success(self):
        self.assertFalse((await self.sync([page(['a', 'b'])], max_pages=1))['success'])
        self.db.reconcile_item_listing.assert_not_called()

    async def test_explicit_has_next_prevents_premature_completion(self):
        result = await self.sync([page(['a'], has_next=True), page(['b'], has_next=False, total_count=2)])
        self.assertTrue(result['success'])
        self.assertEqual(result['total_pages'], 2)

    async def test_duplicate_ids_in_or_between_pages_are_not_complete(self):
        for responses in ([page(['a', 'a'])], [page(['a', 'b']), page(['a'])]):
            self.assertFalse((await self.sync(responses))['success'])
        self.db.reconcile_item_listing.assert_not_called()

    async def test_malformed_incomplete_and_inconsistent_pages_are_rejected(self):
        for response in (
            {'success': True},
            page(['']), page(['auto_invalid']),
            page(['a'], saved_count=0),
            page([], has_next=True), page(['a'], has_next='false'),
            page(['a'], total_count=3),
        ):
            self.assertFalse((await self.sync([response]))['success'])
        self.db.reconcile_item_listing.assert_not_called()

    async def test_same_account_conflict_and_different_account_independence(self):
        with item_sync_slot(self.runtime.cookie_id):
            result = await sync_all_item_pages(self.runtime, self.db)
            self.assertFalse(result['success'])
            self.runtime.get_item_list_info.assert_not_awaited()
            with item_sync_slot('another-account'):
                pass
        self.assertTrue((await self.sync([page([])]))['success'])

    async def test_slot_released_on_exception_and_cancellation(self):
        import asyncio
        for error in (RuntimeError('synthetic error'), asyncio.CancelledError()):
            self.runtime.get_item_list_info.side_effect = error
            if isinstance(error, asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await sync_all_item_pages(self.runtime, self.db)
            else:
                self.assertFalse((await sync_all_item_pages(self.runtime, self.db))['success'])
            with item_sync_slot(self.runtime.cookie_id):
                pass


class ItemListingDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        directory = cls.stack.enter_context(tempfile.TemporaryDirectory())
        cls.stack.enter_context(mock.patch.dict(os.environ, {
            'DB_PATH': str(Path(directory) / 'bootstrap.db'),
            'SECRET_ENCRYPTION_KEY': Fernet.generate_key().decode(),
            'SQL_LOG_ENABLED': 'false',
        }))
        cls.stack.enter_context(mock.patch.object(logger, '_log'))
        module = importlib.import_module('db_manager')
        cls.database_type = module.DBManager
        cls.db = module.DBManager(str(Path(directory) / 'listings.db'))
        cls.stack.callback(cls.db.close)

    def setUp(self):
        self.db.conn.execute('DELETE FROM item_info')
        for account, item in (('a', 'kept'), ('a', 'removed'), ('b', 'other')):
            self.db.conn.execute(
                'INSERT INTO item_info (cookie_id, item_id, item_title, item_detail) VALUES (?, ?, ?, ?)',
                (account, item, 'Synthetic title', 'Synthetic historical detail'),
            )
        self.db.conn.commit()

    def test_reconciliation_preserves_history_and_other_accounts(self):
        count = self.db.reconcile_item_listing('a', {'kept'}, time.time_ns())
        self.assertEqual(count, 1)
        self.assertEqual([i['item_id'] for i in self.db.get_items_by_cookie('a', listed_only=True)], ['kept'])
        row = self.db.get_item_info('a', 'removed')
        self.assertEqual(row['item_detail'], 'Synthetic historical detail')
        self.assertFalse(row['is_listed'])
        self.assertTrue(self.db.get_item_info('b', 'other')['is_listed'])

    def test_empty_snapshot_and_reappearing_item(self):
        self.assertEqual(self.db.reconcile_item_listing('a', set(), time.time_ns()), 2)
        self.assertEqual(self.db.mark_items_listed('a', ['removed']), 1)
        self.assertTrue(self.db.get_item_info('a', 'removed')['is_listed'])
        self.assertFalse(self.db.get_item_info('a', 'kept')['is_listed'])

    def test_observed_during_sync_not_mistakenly_unlisted(self):
        started = time.time_ns()
        self.db.mark_items_listed('a', ['removed'])
        self.db.reconcile_item_listing('a', {'kept'}, started)
        self.assertTrue(self.db.get_item_info('a', 'removed')['is_listed'])

    def test_old_schema_upgrade_keeps_existing_items_listed(self):
        with tempfile.TemporaryDirectory() as directory:
            import sqlite3
            path = str(Path(directory) / 'old.db')
            with sqlite3.connect(path) as connection:
                connection.execute('''CREATE TABLE item_info (
                    id INTEGER PRIMARY KEY, cookie_id TEXT, item_id TEXT, item_title TEXT,
                    item_description TEXT, item_category TEXT, item_price TEXT, item_detail TEXT,
                    is_multi_spec BOOLEAN, created_at TIMESTAMP, updated_at TIMESTAMP,
                    UNIQUE(cookie_id, item_id))''')
                connection.execute("INSERT INTO item_info (cookie_id,item_id,item_title) VALUES ('a','legacy','Legacy')")
            db = self.database_type(path)
            try:
                self.assertTrue(db.get_item_info('a', 'legacy')['is_listed'])
            finally:
                db.close()


class ItemPageParsingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'XianyuAutoAsync.py'
        tree = ast.parse(source.read_text())
        live = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'XianyuLive')
        method = next(n for n in live.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'get_item_list_info')
        self.database = mock.Mock()
        self.database.mark_items_listed.return_value = 1
        namespace = {
            'logger': mock.Mock(), 'time': time, 'json': importlib.import_module('json'),
            'asyncio': SimpleNamespace(sleep=mock.AsyncMock()), 'db_manager': self.database,
            'normalize_item_list_page_size': lambda size: size,
            'trans_cookies': lambda _: {'_m_h5_tk': 'synthetic_123'},
            'generate_sign': lambda *args: 'synthetic-sign', 'print': lambda *args: None,
        }
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
        self.response = SimpleNamespace(headers={}, json=mock.AsyncMock())
        context = mock.MagicMock()
        context.__aenter__ = mock.AsyncMock(return_value=self.response)
        self.runtime = SimpleNamespace(
            session=SimpleNamespace(post=mock.Mock(return_value=context)),
            cookies_str='synthetic-cookie', cookie_id='a', myid='synthetic-user',
            _apply_response_cookie_updates=mock.AsyncMock(return_value=False),
            save_items_list_to_db=mock.AsyncMock(return_value=1), _safe_str=str,
            _mask_secret_value=lambda *args, **kwargs: '[redacted]',
        )
        self.runtime.get_item_list_info = MethodType(namespace['get_item_list_info'], self.runtime)

    async def test_missing_or_malformed_list_is_not_an_empty_store(self):
        for data in ({}, {'cardList': None}, {'cardList': [{}]}):
            self.response.json.return_value = {'ret': ['SUCCESS::调用成功'], 'data': data}
            self.assertFalse((await self.runtime.get_item_list_info()).get('success', False))
        self.database.mark_items_listed.assert_not_called()

    async def test_valid_empty_page_is_accepted(self):
        self.response.json.return_value = {'ret': ['SUCCESS::调用成功'], 'data': {'cardList': []}}
        result = await self.runtime.get_item_list_info()
        self.assertTrue(result['success'])
        self.assertEqual(result['items'], [])
        self.database.mark_items_listed.assert_not_called()

    async def test_saved_page_restores_listing_and_preserves_pagination(self):
        self.response.json.return_value = {'ret': ['SUCCESS::调用成功'], 'data': {
            'cardList': [{'cardData': {'id': 'a', 'title': 'Synthetic item'}}],
            'hasNext': 'true', 'totalCount': '21',
        }}
        result = await self.runtime.get_item_list_info()
        self.assertTrue(result['success'])
        self.assertTrue(result['has_next'])
        self.assertEqual(result['total_count'], 21)
        self.database.mark_items_listed.assert_called_once()


if __name__ == '__main__':
    unittest.main()
