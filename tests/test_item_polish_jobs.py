import asyncio
import unittest
from unittest import mock

from utils.item_polish_jobs import ItemPolishJobs


class ItemPolishJobTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.jobs = ItemPolishJobs()

    async def asyncTearDown(self):
        tasks = list(self.jobs.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_start_returns_before_work_and_reuses_active_job_with_live_progress(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def run(update):
            await update({'stage': 'polishing', 'total': 10001, 'processed': 501, 'polished': 500, 'failed': 1})
            entered.set()
            await release.wait()
            return {'success': True, 'partial': True, 'total': 10001, 'polished': 10000, 'failed': 1,
                    'message': 'Synthetic completion', 'results': [{'item_id': str(i)} for i in range(10001)]}

        runner = mock.AsyncMock(side_effect=run)
        first = self.jobs.start('a', 1, runner)
        self.assertEqual(first['status'], 'pending')
        runner.assert_not_awaited()
        second = self.jobs.start('a', 1, runner)
        self.assertEqual(first['job_id'], second['job_id'])
        task = self.jobs.tasks[first['job_id']]
        await asyncio.wait_for(entered.wait(), 1)
        progress = self.jobs.get(first['job_id'], 'a', 1)
        self.assertEqual((progress['total'], progress['processed'], progress['polished'], progress['failed']), (10001, 501, 500, 1))
        self.assertEqual(self.jobs.start('a', 1, runner)['job_id'], first['job_id'])
        release.set()
        await task
        result = self.jobs.get(first['job_id'], 'a', 1)
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['result']['partial'])
        self.assertNotIn('results', result['result'])
        self.assertNotIn('user_id', result)
        self.assertEqual(result['processed'], 10001)
        runner.assert_awaited_once()
        self.assertNotIn('a', self.jobs.active)

    async def test_job_queries_and_active_job_reuse_are_isolated_by_account_and_user(self):
        job = self.jobs.start('a', 1, mock.AsyncMock(return_value={'success': True}))
        for account, user in (('b', 1), ('a', 2)):
            with self.assertRaises(PermissionError):
                self.jobs.get(job['job_id'], account, user)
        with self.assertRaises(PermissionError):
            self.jobs.start('a', 2, mock.AsyncMock())
        with self.assertRaises(LookupError):
            self.jobs.get('missing', 'a', 1)

    async def test_failure_releases_account_without_exposing_exception_details(self):
        runner = mock.AsyncMock(side_effect=RuntimeError('synthetic-private-detail'))
        job = self.jobs.start('a', 1, runner)
        await self.jobs.tasks[job['job_id']]
        snapshot = self.jobs.get(job['job_id'], 'a', 1)
        self.assertEqual(snapshot['status'], 'failed')
        self.assertNotIn('synthetic-private-detail', snapshot['message'])
        self.assertNotIn('a', self.jobs.active)

    async def test_cancellation_before_work_starts_releases_active_account(self):
        runner = mock.AsyncMock()
        job = self.jobs.start('a', 1, runner)
        task = self.jobs.tasks[job['job_id']]
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.jobs.get(job['job_id'], 'a', 1)['status'], 'cancelled')
        self.assertNotIn('a', self.jobs.active)
        runner.assert_not_awaited()

    async def test_cancellation_during_work_keeps_last_progress_and_releases_account(self):
        entered = asyncio.Event()

        async def run(update):
            await update({'total': 1001, 'processed': 200, 'polished': 200})
            entered.set()
            await asyncio.Event().wait()

        job = self.jobs.start('a', 1, run)
        task = self.jobs.tasks[job['job_id']]
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        snapshot = self.jobs.get(job['job_id'], 'a', 1)
        self.assertEqual(snapshot['status'], 'cancelled')
        self.assertEqual(snapshot['processed'], 200)
        self.assertNotIn('a', self.jobs.active)

    async def test_retention_only_expires_finished_jobs(self):
        completed = self.jobs.start('a', 1, mock.AsyncMock(return_value={'success': True, 'total': 0}))
        await self.jobs.tasks[completed['job_id']]
        active = self.jobs.start('b', 1, mock.AsyncMock(return_value={'success': True}))
        finished_at = self.jobs.get(completed['job_id'], 'a', 1)['finished_at']
        with mock.patch('utils.item_polish_jobs.time.time', return_value=finished_at + 7200):
            with self.assertRaises(LookupError):
                self.jobs.get(completed['job_id'], 'a', 1)
            self.assertEqual(self.jobs.get(active['job_id'], 'b', 1)['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
