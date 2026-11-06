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
        self.enterContext(mock.patch.object(logger, '_log'))

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
        self.runtime.get_item_list_info.assert_awaited_once()
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

    async def test_large_catalog_fetches_every_page_with_or_without_pagination_metadata(self):
        for total in (1000, 1001, 10001):
            for with_metadata in (False, True):
                with self.subTest(total=total, with_metadata=with_metadata):
                    item_ids = [f'item-{index}' for index in range(total)]

                    async def get_page(page_number, page_size, **kwargs):
                        start = (page_number - 1) * page_size
                        metadata = {'has_next': start + page_size < total, 'total_count': total} if with_metadata else {}
                        self.db.reconcile_item_listing.assert_not_called()
                        return page(item_ids[start:start + page_size], **metadata)

                    self.runtime.get_item_list_info.reset_mock()
                    self.db.reconcile_item_listing.reset_mock()
                    self.runtime.get_item_list_info.side_effect = get_page
                    result = await sync_all_item_pages(self.runtime, self.db)
                    self.assertTrue(result['success'])
                    self.assertTrue(result['complete'])
                    self.assertEqual(result['attempts'], 1)
                    self.assertEqual(result['total_count'], total)
                    self.assertEqual([item['id'] for item in result['items']], item_ids)
                    expected_pages = (total + 19) // 20 if with_metadata else total // 20 + 1
                    self.assertEqual(result['total_pages'], expected_pages)
                    self.assertEqual(self.runtime.get_item_list_info.await_count, expected_pages)
                    self.assertEqual(self.runtime.get_item_list_info.await_args.args[0], expected_pages)
                    self.db.reconcile_item_listing.assert_called_once()
                    self.assertEqual(self.db.reconcile_item_listing.call_args.args[1], set(item_ids))

    async def test_large_catalog_recovers_late_missing_items_with_smaller_pages(self):
        item_ids = [f'item-{index}' for index in range(1001)]
        attempt = 0

        async def get_page(page_number, page_size, **kwargs):
            nonlocal attempt
            if page_number == 1:
                attempt += 1
            self.db.reconcile_item_listing.assert_not_called()
            start = (page_number - 1) * page_size
            current_ids = item_ids[start:start + page_size]
            if attempt < 3 and page_number == 50:
                current_ids = current_ids[:10]
            return page(current_ids, has_next=start + page_size < len(item_ids), total_count=len(item_ids))

        self.runtime.get_item_list_info.side_effect = get_page
        result = await sync_all_item_pages(self.runtime, self.db)
        self.assertTrue(result['success'])
        self.assertEqual(result['attempts'], 3)
        self.assertEqual(result['total_count'], 1001)
        self.assertEqual(result['total_saved'], 1001)
        self.assertEqual(result['total_pages'], 101)
        self.assertEqual([item['id'] for item in result['items']], item_ids)
        self.assertEqual(self.runtime.get_item_list_info.await_args.args, (101, 10))
        self.db.reconcile_item_listing.assert_called_once()
        self.assertEqual(self.db.reconcile_item_listing.call_args.args[1], set(item_ids))

    async def test_duplicate_ids_in_or_between_pages_are_not_complete(self):
        for responses in ([page(['a', 'a'])], [page(['a', 'b']), page(['a'])]):
            self.runtime.get_item_list_info.reset_mock()
            result = await self.sync(responses * 3)
            self.assertFalse(result['success'])
            self.assertEqual(result['attempts'], 3)
            self.assertIn('重复记录', result['error'])
            self.assertEqual(self.runtime.get_item_list_info.await_count, len(responses) * 3)
        self.db.reconcile_item_listing.assert_not_called()

    async def test_malformed_incomplete_and_inconsistent_pages_are_rejected(self):
        for response in (
            {'success': True},
            page(['']), page(['auto_invalid']),
            page(['a'], saved_count=0),
            page(['a'], has_next='false'),
            page(['a'], total_count=-1), page(['a'], total_count=True),
        ):
            self.runtime.get_item_list_info.reset_mock()
            self.assertFalse((await self.sync([response]))['success'])
            self.runtime.get_item_list_info.assert_awaited_once()
        self.db.reconcile_item_listing.assert_not_called()

    async def test_read_mode_skips_reconciliation_even_for_complete_or_empty_lists(self):
        for ids in (['a'], []):
            with self.subTest(ids=ids):
                result = await self.sync(
                    [page(ids, has_next=False, total_count=len(ids))],
                    reconcile_listing=False,
                )
                self.assertTrue(result['success'])
                self.assertTrue(result['complete'])
                self.assertFalse(result['partial'])
                self.assertEqual(result['unlisted_count'], 0)
        self.db.reconcile_item_listing.assert_not_called()

    async def test_short_page_continues_using_total_from_an_earlier_page(self):
        result = await self.sync([
            page(['a'], total_count=3),
            page(['b', 'c']),
            page([]),
        ])
        self.assertTrue(result['success'])
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(result['total_pages'], 3)
        self.assertEqual([item['id'] for item in result['items']], ['a', 'b', 'c'])
        self.db.reconcile_item_listing.assert_called_once()

    async def test_premature_end_flag_is_probed_until_total_is_reached(self):
        result = await self.sync([
            page(['a'], has_next=False, total_count=3),
            page(['b', 'c'], has_next=False, total_count=3),
        ])
        self.assertTrue(result['success'])
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(result['total_count'], 3)
        self.assertEqual(self.runtime.get_item_list_info.await_count, 2)

    async def test_overflow_mismatch_error_reports_actual_numbers(self):
        """报错必须带实际数字，否则维护者无从定位（issue #122）。

        场景：平台说总共 2 条，实际却返回了 3 条。三轮都这样 → 最终失败，
        错误文案里要能直接看到「已抓取 3 条，平台返回 2 条」。
        """
        result = await self.sync([page(['a', 'b', 'c'], has_next=True, total_count=2)] * 3)
        self.assertFalse(result['success'])
        self.assertEqual(result['attempts'], 3)
        self.assertIn('已抓取 3 条', result['error'])
        self.assertIn('平台返回 2 条', result['error'])
        self.db.reconcile_item_listing.assert_not_called()

    async def test_shortfall_mismatch_error_reports_per_page_sizes(self):
        """各页条数明细是区分「漏页」和「口径不同」的关键证据。

        [1, 0] 表示第一页不满、第二页为空；保留这些观察值，
        供排查分页遗漏或平台统计口径，不能仅凭条数断言根因。
        """
        result = await self.sync(
            [page(['stale'], has_next=False, total_count=2),
             page([], has_next=False, total_count=2)] * 3
        )
        self.assertFalse(result['success'])
        self.assertEqual(result['attempts'], 3)
        self.assertIn('已抓取 1 条', result['error'])
        self.assertIn('平台返回 2 条', result['error'])
        self.assertIn('各页条数', result['error'])
        self.assertIn('[1, 0]', result['error'])
        self.assertIn('每页请求 1 条', result['error'])
        self.db.reconcile_item_listing.assert_not_called()

    async def test_count_mismatch_restarts_without_merging_different_snapshots(self):
        result = await self.sync([
            page(['stale'], has_next=False, total_count=2),
            page([], has_next=False, total_count=2),
            page(['a', 'b'], has_next=False, total_count=2),
        ], sync_item_details=True)
        self.assertTrue(result['success'])
        self.assertTrue(result['complete'])
        self.assertFalse(result['partial'])
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(result['total_pages'], 1)
        self.assertEqual(result['total_saved'], 2)
        self.assertEqual([item['id'] for item in result['items']], ['a', 'b'])
        self.assertEqual(self.runtime.get_item_list_info.await_args_list, [
            mock.call(1, 2, sync_item_details=True),
            mock.call(2, 2, sync_item_details=True),
            mock.call(1, 2, sync_item_details=True),
        ])
        self.db.reconcile_item_listing.assert_called_once()
        self.assertEqual(self.db.reconcile_item_listing.call_args.args[1], {'a', 'b'})

    async def test_last_attempt_uses_smaller_pages_to_recover_missing_items(self):
        missing_tail = [page(['a'], has_next=False, total_count=2), page([], has_next=False, total_count=2)]
        result = await self.sync(missing_tail * 2 + [
            page(['a'], has_next=True, total_count=2),
            page(['b'], has_next=False, total_count=2),
        ])
        self.assertTrue(result['success'])
        self.assertEqual(result['attempts'], 3)
        self.assertEqual(result['total_pages'], 2)
        self.assertEqual(result['total_saved'], 2)
        self.assertEqual(self.runtime.get_item_list_info.await_args_list, [
            mock.call(1, 2, sync_item_details=False),
            mock.call(2, 2, sync_item_details=False),
            mock.call(1, 2, sync_item_details=False),
            mock.call(2, 2, sync_item_details=False),
            mock.call(1, 1, sync_item_details=False),
            mock.call(2, 1, sync_item_details=False),
        ])
        self.db.reconcile_item_listing.assert_called_once()

    async def test_persistent_total_mismatch_fails_after_three_attempts_without_reconciliation(self):
        for reconcile in (False, True):
            with self.subTest(reconcile=reconcile):
                self.runtime.get_item_list_info.reset_mock()
                result = await self.sync([
                    page(['a'], has_next=False, total_count=3),
                    page([], has_next=False),
                ] * 3, reconcile_listing=reconcile)
                self.assertFalse(result['success'])
                self.assertFalse(result['complete'])
                self.assertTrue(result['partial'])
                self.assertIn('3 轮', result['error'])
                self.assertIn('总数不一致', result['error'])
                self.assertEqual(result['attempts'], 3)
                self.assertEqual(self.runtime.get_item_list_info.await_count, 6)
                self.assertEqual(result['total_count'], 1)
                self.assertEqual(result['total_saved'], 1)
                self.assertEqual(result['unlisted_count'], 0)
        self.db.reconcile_item_listing.assert_not_called()

    async def test_total_changes_between_pages_trigger_a_fresh_snapshot(self):
        result = await self.sync([
            page(['stale', 'a'], has_next=True, total_count=3),
            page(['b'], has_next=False, total_count=4),
            page(['a', 'b'], has_next=False, total_count=2),
        ])
        self.assertTrue(result['success'])
        self.assertEqual(result['attempts'], 2)
        self.assertEqual([item['id'] for item in result['items']], ['a', 'b'])
        self.assertEqual(self.db.reconcile_item_listing.call_args.args[1], {'a', 'b'})

    async def test_duplicate_page_can_recover_by_restarting_at_page_one(self):
        result = await self.sync([
            page(['a', 'b'], has_next=True, total_count=3),
            page(['b', 'c'], has_next=False, total_count=3),
            page(['b', 'c'], has_next=False, total_count=2),
        ])
        self.assertTrue(result['success'])
        self.assertEqual(result['attempts'], 2)
        self.assertEqual([item['id'] for item in result['items']], ['b', 'c'])
        self.db.reconcile_item_listing.assert_called_once()

    async def test_missing_total_on_the_last_page_does_not_hide_a_mismatch(self):
        async def get_page(page_number, page_size, **kwargs):
            if page_number == 1:
                return page(['a'], has_next=True, total_count=3)
            if page_number == 2:
                return page(['b'], has_next=False)
            return page([], has_next=False)

        self.runtime.get_item_list_info.side_effect = get_page
        result = await sync_all_item_pages(self.runtime, self.db, page_size=2)
        self.assertFalse(result['success'])
        self.assertEqual(result['attempts'], 3)
        self.assertIn('总数不一致', result['error'])
        self.db.reconcile_item_listing.assert_not_called()

    async def test_contradictory_empty_pages_or_totals_never_allow_reconciliation(self):
        for response in (
            page([], has_next=False, total_count=3),
            page([], has_next=True),
            page(['a'], has_next=False, total_count=0),
        ):
            with self.subTest(response=response):
                self.runtime.get_item_list_info.reset_mock()
                result = await self.sync([response] * 3)
                self.assertFalse(result['success'])
                self.assertFalse(result['complete'])
                self.assertEqual(result['attempts'], 3)
                self.assertEqual(self.runtime.get_item_list_info.await_count, 3)
        self.db.reconcile_item_listing.assert_not_called()

    async def test_probing_does_not_bypass_maximum_pages(self):
        result = await self.sync([page(['a'], has_next=False, total_count=3)], max_pages=1)
        self.assertFalse(result['success'])
        self.assertEqual(result['attempts'], 1)
        self.runtime.get_item_list_info.assert_awaited_once()
        self.db.reconcile_item_listing.assert_not_called()

    async def test_retry_keeps_the_account_slot_and_releases_it_on_cancellation(self):
        import asyncio

        async def cancel_during_retry(delay):
            with self.assertRaises(ItemSyncInProgress):
                with item_sync_slot(self.runtime.cookie_id):
                    pass
            raise asyncio.CancelledError()

        with mock.patch('utils.item_sync.asyncio.sleep', side_effect=cancel_during_retry):
            with self.assertRaises(asyncio.CancelledError):
                await self.sync([page(['a'], has_next=False, total_count=0)])
        self.db.reconcile_item_listing.assert_not_called()
        with item_sync_slot(self.runtime.cookie_id):
            pass

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

    def test_seen_exactly_at_sync_start_is_not_unlisted(self):
        """边界必须严格小于才算"同步前观察到"。

        场景：同步开始时刻 `started_ns` 与被观察时刻完全相同。它算"同步期间
        观察到"，必须保留。原来写 `<=` 会把它当"同步前"下架。

        这条用**确定值**锁定语义，不依赖 time.time_ns() 的精度 —— 上面那条
        `test_observed_during_sync_not_mistakenly_unlisted` 靠两次真实取时间，
        万一落在同一纳秒就随机失败（实测负载下约 1/5 概率），定位成本很高。
        """
        fixed_ns = 1_800_000_000_000_000_000
        with mock.patch('db_manager.time.time_ns', return_value=fixed_ns):
            self.db.mark_items_listed('a', ['removed'])
        # 同步起点与观察时刻相同
        self.db.reconcile_item_listing('a', {'kept'}, fixed_ns)
        self.assertTrue(
            self.db.get_item_info('a', 'removed')['is_listed'],
            '与同步起点同一时刻观察到的商品不应被下架',
        )

    def test_seen_before_sync_start_is_unlisted(self):
        """严格早于起点、又不在本次快照里 → 应该被软下架。"""
        seen_ns = 1_800_000_000_000_000_000
        with mock.patch('db_manager.time.time_ns', return_value=seen_ns):
            self.db.mark_items_listed('a', ['removed'])
        self.assertEqual(self.db.reconcile_item_listing('a', {'kept'}, seen_ns + 1), 1)
        self.assertFalse(self.db.get_item_info('a', 'removed')['is_listed'])

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
