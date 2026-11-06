import ast
import asyncio
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, Dict
import unittest
from unittest import mock
import uuid

from fastapi import Depends, FastAPI, HTTPException
import httpx

from utils.item_polish_jobs import ItemPolishJobs


class ItemPolishApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = FastAPI()
        self.jobs = ItemPolishJobs()
        self.db = mock.Mock()
        self.db.get_cookie_by_id.return_value = {'cookies_str': 'synthetic-cookie'}
        self.user = {'user_id': 1}
        self.executor = mock.AsyncMock(return_value={'success': True, 'total': 1, 'polished': 1, 'failed': 0})

        def ensure_access(account_id, user):
            if {'a': 1, 'b': 2}.get(account_id) != user['user_id']:
                raise HTTPException(status_code=403, detail='无权访问该账号')
            return account_id

        namespace = {
            'app': self.app, 'Depends': Depends, 'HTTPException': HTTPException,
            'Dict': Dict, 'Any': Any, 'get_current_user': lambda: self.user,
            '_ensure_cookie_access': ensure_access, 'db_manager': self.db,
            'item_polish_jobs': self.jobs, 'uuid': uuid, 'asyncio': asyncio, 'logger': mock.Mock(),
        }
        source = Path(__file__).resolve().parents[1] / 'reply_server.py'
        tree = ast.parse(source.read_text())
        names = {'_execute_manual_item_polish', 'polish_account_items', 'get_item_polish_job'}
        nodes = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name in names]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
        self.execute_real = namespace['_execute_manual_item_polish']
        namespace['_execute_manual_item_polish'] = self.executor
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://polish.test')

    async def asyncTearDown(self):
        tasks = list(self.jobs.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.aclose()

    async def test_background_start_returns_without_waiting_and_reuses_running_job(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def execute(account_id, cookies, progress):
            await progress({'stage': 'polishing', 'total': 10001, 'processed': 501, 'polished': 501})
            entered.set()
            await release.wait()
            return {'success': True, 'total': 10001, 'polished': 10001, 'failed': 0, 'message': 'Synthetic completion'}

        self.executor.side_effect = execute
        first = await asyncio.wait_for(self.client.post('/accounts/a/polish-items?background=true'), 1)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()['accepted'])
        job_id = first.json()['job']['job_id']
        task = self.jobs.tasks[job_id]
        await asyncio.wait_for(entered.wait(), 1)
        status = await self.client.get(f'/accounts/a/polish-items/{job_id}')
        self.assertEqual(status.json()['job']['processed'], 501)
        second = await self.client.post('/accounts/a/polish-items?background=true')
        self.assertEqual(second.json()['job']['job_id'], job_id)
        self.executor.assert_awaited_once()
        release.set()
        await task
        finished = (await self.client.get(f'/accounts/a/polish-items/{job_id}')).json()['job']
        self.assertEqual(finished['status'], 'completed')
        self.assertEqual(finished['polished'], 10001)

    async def test_status_checks_both_account_access_and_job_ownership(self):
        job = self.jobs.start('a', 1, mock.AsyncMock(return_value={'success': True}))
        job_id = job['job_id']
        self.user = {'user_id': 2}
        self.assertEqual((await self.client.get(f'/accounts/a/polish-items/{job_id}')).status_code, 403)
        self.assertEqual((await self.client.get(f'/accounts/b/polish-items/{job_id}')).status_code, 403)
        self.assertEqual((await self.client.post('/accounts/a/polish-items?background=true')).status_code, 403)
        self.user = {'user_id': 1}
        self.assertEqual((await self.client.get('/accounts/a/polish-items/missing')).status_code, 404)
        self.executor.assert_not_awaited()

    async def test_legacy_post_returns_final_result(self):
        response = await self.client.post('/accounts/a/polish-items')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['polished'], 1)
        self.assertNotIn('accepted', response.json())
        self.executor.assert_awaited_once_with('a', 'synthetic-cookie')

    async def test_executor_closes_session_and_records_interruption_on_cancellation(self):
        runtime = SimpleNamespace(polish_all_items=mock.AsyncMock(side_effect=asyncio.CancelledError()), close_session=mock.AsyncMock())
        module = ModuleType('XianyuAutoAsync')
        module.XianyuLive = mock.Mock(return_value=runtime)
        with mock.patch.dict(sys.modules, {'XianyuAutoAsync': module}):
            with self.assertRaises(asyncio.CancelledError):
                await self.execute_real('a', 'synthetic-cookie')
        runtime.close_session.assert_awaited_once()
        self.assertEqual(self.db.add_scheduled_task_log.call_args.kwargs['status'], 'failed')

    async def test_executor_preserves_partial_result_in_task_log(self):
        result = {'success': True, 'partial': True, 'total': 2, 'polished': 1, 'failed': 1, 'message': 'Synthetic partial result'}
        runtime = SimpleNamespace(polish_all_items=mock.AsyncMock(return_value=result), close_session=mock.AsyncMock())
        module = ModuleType('XianyuAutoAsync')
        module.XianyuLive = mock.Mock(return_value=runtime)
        with mock.patch.dict(sys.modules, {'XianyuAutoAsync': module}):
            self.assertEqual(await self.execute_real('a', 'synthetic-cookie'), result)
        runtime.close_session.assert_awaited_once()
        log = self.db.add_scheduled_task_log.call_args.kwargs
        self.assertEqual(log['status'], 'partial_success')
        self.assertEqual(log['raw_response'], result)


if __name__ == '__main__':
    unittest.main()
