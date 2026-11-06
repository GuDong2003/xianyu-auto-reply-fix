"""擦亮回归：模拟分页与擦亮响应，不启动真实账号或后台任务。"""

import ast
import asyncio
from pathlib import Path
import sys
from types import MethodType, ModuleType, SimpleNamespace
import unittest
from unittest import mock

from loguru import logger

with mock.patch.object(logger, '_log'):
    from item_polish_module import ItemPolishModule

from utils.item_sync import sync_all_item_pages


def page(ids, **extra):
    return {'success': True, 'items': [{'id': i} for i in ids], 'saved_count': len(ids), **extra}


class ItemPolishTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[1]
        source = cls.repo / 'XianyuAutoAsync.py'
        tree = ast.parse(source.read_text())
        live = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'XianyuLive')
        cls.list_method = next(node for node in live.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'get_all_items')

    def setUp(self):
        self.enterContext(mock.patch.object(logger, '_log'))
        self.enterContext(mock.patch('utils.item_sync.asyncio.sleep', new=mock.AsyncMock()))
        self.db = mock.Mock()
        namespace = {'db_manager': self.db, 'sync_all_item_pages': sync_all_item_pages}
        exec(compile(ast.Module(body=[self.list_method], type_ignores=[]), 'XianyuAutoAsync.py', 'exec'), namespace)
        self.runtime = SimpleNamespace(cookie_id='synthetic-account', get_item_list_info=mock.AsyncMock())
        self.runtime.get_all_items = MethodType(namespace['get_all_items'], self.runtime)
        self.module = ItemPolishModule(self.runtime)
        self.module.polish_item = mock.AsyncMock(side_effect=lambda item_id: {'success': True, 'item_id': item_id})

    async def run_polish(self, responses):
        self.runtime.get_item_list_info.side_effect = responses
        return await self.module.polish_all_items()

    async def test_count_mismatch_refetches_complete_snapshot_before_polishing(self):
        async def polish(item_id):
            self.assertEqual(self.runtime.get_item_list_info.await_count, 4)
            return {'success': True, 'item_id': item_id}

        self.module.polish_item.side_effect = polish
        result = await self.run_polish([
            page(['a', 'b'], has_next=True, total_count=4),
            page(['c'], has_next=False, total_count=4),
            page([], has_next=False, total_count=4),
            page(['a', 'b', 'c', 'd'], has_next=False, total_count=4),
        ])
        self.assertTrue(result['success'])
        self.assertFalse(result['partial'])
        self.assertTrue(result['list_complete'])
        self.assertEqual((result['total'], result['polished'], result['failed']), (4, 4, 0))
        self.assertNotIn('warning', result)
        self.assertEqual(self.module.polish_item.await_args_list, [mock.call(item) for item in ('a', 'b', 'c', 'd')])
        self.assertEqual([call.args[0] for call in self.runtime.get_item_list_info.await_args_list], [1, 2, 3, 1])
        self.db.reconcile_item_listing.assert_not_called()

    async def test_short_page_with_total_mismatch_fetches_the_remaining_items(self):
        result = await self.run_polish([
            page(['a'], total_count=3),
            page(['b', 'c'], has_next=False, total_count=3),
        ])
        self.assertTrue(result['success'])
        self.assertFalse(result['partial'])
        self.assertTrue(result['list_complete'])
        self.assertEqual(result['polished'], 3)
        self.assertEqual(self.module.polish_item.await_args_list, [mock.call('a'), mock.call('b'), mock.call('c')])
        self.db.reconcile_item_listing.assert_not_called()

    async def test_large_catalog_is_fully_collected_then_polished_once_per_item(self):
        item_ids = [f'item-{index}' for index in range(1001)]

        async def get_page(page_number, page_size, **kwargs):
            self.module.polish_item.assert_not_awaited()
            start = (page_number - 1) * page_size
            return page(
                item_ids[start:start + page_size],
                has_next=start + page_size < len(item_ids), total_count=len(item_ids),
            )

        self.runtime.get_item_list_info.side_effect = get_page
        result = await self.module.polish_all_items()
        self.assertTrue(result['success'])
        self.assertTrue(result['list_complete'])
        self.assertFalse(result['partial'])
        self.assertEqual((result['total'], result['polished'], result['failed']), (1001, 1001, 0))
        self.assertEqual(self.runtime.get_item_list_info.await_count, 51)
        self.assertEqual([row['item_id'] for row in result['results']], item_ids)
        self.assertEqual(self.module.polish_item.await_args_list, [mock.call(item_id) for item_id in item_ids])
        self.db.reconcile_item_listing.assert_not_called()

    async def test_three_incomplete_attempts_stop_before_any_polishing(self):
        result = await self.run_polish([
            page(['a'], has_next=False, total_count=2),
            page([], has_next=False, total_count=2),
        ] * 3)
        self.assertFalse(result['success'])
        self.assertFalse(result['list_complete'])
        self.assertEqual((result['total'], result['polished'], result['failed']), (0, 0, 0))
        self.assertIn('3 轮', result['message'])
        self.assertEqual(self.runtime.get_item_list_info.await_count, 6)
        self.module.polish_item.assert_not_awaited()
        self.db.reconcile_item_listing.assert_not_called()

    async def test_progress_reports_total_and_actual_success_or_failure_counts(self):
        progress = mock.AsyncMock()
        self.runtime.get_item_list_info.side_effect = [page(['a', 'b'], has_next=False, total_count=2)]
        self.module.polish_item.side_effect = [{'success': True}, {'success': False}]
        result = await self.module.polish_all_items(on_progress=progress)
        self.assertTrue(result['partial'])
        updates = [call.args[0] for call in progress.await_args_list]
        self.assertEqual([update['processed'] for update in updates], [0, 1, 2])
        self.assertEqual((updates[-1]['total'], updates[-1]['polished'], updates[-1]['failed']), (2, 1, 1))

    async def test_concurrent_polishing_is_rejected_until_the_first_task_finishes(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def polish(item_id):
            entered.set()
            await release.wait()
            return {'success': True, 'item_id': item_id}

        self.runtime.get_item_list_info.return_value = page(['a'], has_next=False, total_count=1)
        self.module.polish_item.side_effect = polish
        first = asyncio.create_task(self.module.polish_all_items())
        self.addAsyncCleanup(lambda: asyncio.gather(first, return_exceptions=True))
        self.addCleanup(release.set)
        await asyncio.wait_for(entered.wait(), 1)
        second = await ItemPolishModule(self.runtime).polish_all_items()
        self.assertFalse(second['success'])
        self.assertTrue(second['busy'])
        self.runtime.get_item_list_info.assert_awaited_once()
        release.set()
        self.assertTrue((await first)['success'])
        self.assertTrue((await self.module.polish_all_items())['success'])

    async def test_successful_but_incomplete_list_response_never_starts_polishing(self):
        with mock.patch.object(self.runtime, 'get_all_items', return_value={
            'success': True, 'complete': False, 'items': [{'id': 'a'}],
        }):
            result = await self.module.polish_all_items()
        self.assertFalse(result['success'])
        self.assertIn('无法确认完整商品列表', result['message'])
        self.module.polish_item.assert_not_awaited()
        self.db.reconcile_item_listing.assert_not_called()

    async def test_complete_list_polishes_without_unlisting_other_items(self):
        result = await self.run_polish([page(['a', 'b'], has_next=False, total_count=2)])
        self.assertTrue(result['success'])
        self.assertTrue(result['list_complete'])
        self.assertFalse(result['partial'])
        self.assertNotIn('warning', result)
        self.assertEqual(result['polished'], 2)
        self.db.reconcile_item_listing.assert_not_called()

    async def test_valid_empty_list_reports_no_items_without_reconciliation(self):
        result = await self.run_polish([page([], has_next=False, total_count=0)])
        self.assertTrue(result['success'])
        self.assertTrue(result['list_complete'])
        self.assertFalse(result['partial'])
        self.assertEqual(result['message'], '没有在售商品需要擦亮')
        self.module.polish_item.assert_not_awaited()
        self.db.reconcile_item_listing.assert_not_called()

    async def test_page_errors_and_invalid_items_still_block_all_polishing(self):
        for responses in (
            [{'success': False, 'error': '登录失效'}],
            [page(['a'], has_next=True), {'success': False, 'error': '分页失败'}],
            [RuntimeError('Synthetic page failure')],
            [{'success': True, 'items': None}],
            [page([''])], [page(['auto_invalid'])],
            [page(['a'], saved_count=0)],
            [page(['a', 'a'])] * 3,
            [page(['a'], has_next=True), page(['a'], has_next=False)] * 3,
            [page(['a'], has_next='false')],
            [page([], has_next=True)] * 3,
            [page([], has_next=False, total_count=3)] * 3,
        ):
            with self.subTest(responses=responses):
                result = await self.run_polish(responses)
                self.assertFalse(result['success'])
                self.assertEqual(result['polished'], 0)
                self.assertIn('获取商品列表失败', result['message'])
                self.module.polish_item.assert_not_awaited()
        self.db.reconcile_item_listing.assert_not_called()

    async def test_item_failures_are_counted_and_reported_as_partial(self):
        self.module.polish_item.side_effect = [
            {'success': True, 'item_id': 'a'},
            {'success': False, 'item_id': 'b', 'error': 'Synthetic failure'},
        ]
        result = await self.run_polish([page(['a', 'b'], has_next=False, total_count=2)])
        self.assertTrue(result['list_complete'])
        self.assertTrue(result['partial'])
        self.assertEqual((result['polished'], result['failed']), (1, 1))
        self.assertIn('失败 1', result['message'])

    async def test_live_wrapper_keeps_strict_defaults_for_other_callers(self):
        self.runtime.get_item_list_info.side_effect = [
            page(['a'], total_count=2), page([], has_next=False),
        ] * 3
        result = await self.runtime.get_all_items(sync_item_details=False)
        self.assertFalse(result['success'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['attempts'], 3)
        self.db.reconcile_item_listing.assert_not_called()

    async def run_scheduled_polish(self, responses):
        source = self.repo / 'reply_server.py'
        tree = ast.parse(source.read_text())
        method = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'scheduled_task_checker')
        self.runtime.get_item_list_info.side_effect = responses
        self.runtime.polish_all_items = self.module.polish_all_items
        self.runtime.close_session = mock.AsyncMock()
        self.db.get_due_tasks.return_value = [{
            'id': 1, 'account_id': self.runtime.cookie_id,
            'task_type': 'item_polish', 'name': 'Synthetic polish task',
        }]
        self.db.get_cookie_by_id.return_value = {'cookies_str': 'synthetic-cookie'}
        self.db.calculate_next_daily_run.return_value = '2026-11-07 08:00:00'
        namespace = {
            'db_manager': self.db, 'logger': mock.Mock(),
            'asyncio': SimpleNamespace(sleep=mock.AsyncMock(side_effect=asyncio.CancelledError())),
            'uuid': SimpleNamespace(uuid4=lambda: 'synthetic-batch'),
            'TASK_LOG_TYPE_LABELS': {'item_polish': '商品擦亮'},
        }
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
        live_module = ModuleType('XianyuAutoAsync')
        live_module.XianyuLive = mock.Mock(return_value=self.runtime)
        with mock.patch.dict(sys.modules, {'XianyuAutoAsync': live_module}):
            with self.assertRaises(asyncio.CancelledError):
                await namespace['scheduled_task_checker']()

        stored_result = self.db.update_task_run_result.call_args.args[1]
        log = self.db.add_scheduled_task_log.call_args.kwargs
        self.runtime.close_session.assert_awaited_once()
        self.db.reconcile_item_listing.assert_not_called()
        self.assertEqual(log['message'], stored_result['message'])
        return stored_result, log

    async def test_scheduler_automatically_recovers_before_logging_success(self):
        stored_result, log = await self.run_scheduled_polish([
            page(['a'], has_next=False, total_count=2),
            page([], has_next=False, total_count=2),
            page(['a', 'b'], has_next=False, total_count=2),
        ])
        self.assertTrue(stored_result['success'])
        self.assertFalse(stored_result['partial'])
        self.assertTrue(stored_result['list_complete'])
        self.assertEqual(log['status'], 'success')
        self.assertNotIn('warning', stored_result)
        self.assertEqual(self.module.polish_item.await_args_list, [mock.call('a'), mock.call('b')])

    async def test_scheduler_logs_failure_without_polishing_if_retries_cannot_recover(self):
        stored_result, log = await self.run_scheduled_polish([
            page(['a'], has_next=False, total_count=2),
            page([], has_next=False, total_count=2),
        ] * 3)
        self.assertFalse(stored_result['success'])
        self.assertFalse(stored_result['list_complete'])
        self.assertIn('3 轮', stored_result['message'])
        self.assertEqual(log['status'], 'failed')
        self.module.polish_item.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
