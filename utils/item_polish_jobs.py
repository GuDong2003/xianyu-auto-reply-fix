"""手动擦亮后台任务：同账号复用活动任务，按用户和账号隔离进度。"""

import asyncio
import time
import uuid


class ItemPolishJobs:
    def __init__(self, retention_seconds=3600):
        self.retention_seconds = retention_seconds
        self.jobs = {}
        self.active = {}
        self.tasks = {}

    def _cleanup(self):
        now = time.time()
        for job_id, job in list(self.jobs.items()):
            finished = job.get('finished_at')
            if finished is not None and now - finished > self.retention_seconds:
                self.jobs.pop(job_id, None)

    @staticmethod
    def _snapshot(job):
        return {key: job.get(key) for key in (
            'job_id', 'status', 'stage', 'message', 'total', 'processed',
            'polished', 'failed', 'created_at', 'finished_at', 'result',
        )}

    def start(self, account_id, user_id, run):
        """在请求所属事件循环中调用；任务创建前登记，连续点击复用同一任务。"""
        self._cleanup()
        active_id = self.active.get(account_id)
        if active_id:
            return self.get(active_id, account_id, user_id)
        job_id = f'item_polish_{uuid.uuid4().hex}'
        job = {
            'job_id': job_id, 'account_id': account_id, 'user_id': user_id,
            'status': 'pending', 'stage': 'fetching', 'message': '正在获取完整商品列表',
            'total': 0, 'processed': 0, 'polished': 0, 'failed': 0,
            'created_at': time.time(), 'finished_at': None, 'result': None,
        }
        self.jobs[job_id] = job
        self.active[account_id] = job_id
        task = asyncio.create_task(self._run(job, run))
        self.tasks[job_id] = task
        task.add_done_callback(lambda done: self._task_done(job, done))
        return self._snapshot(job)

    def _task_done(self, job, task):
        # 尚未开始就被取消时，协程中的 finally 不会执行。
        if task.cancelled() and job['finished_at'] is None:
            job.update(status='cancelled', stage='finished', message='擦亮任务已中断，请查看任务日志')
            self._finish(job)

    def _finish(self, job):
        job['finished_at'] = time.time()
        if self.active.get(job['account_id']) == job['job_id']:
            self.active.pop(job['account_id'], None)
        self.tasks.pop(job['job_id'], None)

    def get(self, job_id, account_id, user_id):
        self._cleanup()
        job = self.jobs.get(job_id)
        if job is None:
            raise LookupError('擦亮任务不存在或已过期')
        if job['account_id'] != account_id or job['user_id'] != user_id:
            raise PermissionError('无权访问该擦亮任务')
        return self._snapshot(job)

    async def _run(self, job, run):
        async def update(progress):
            for key in ('stage', 'message', 'total', 'processed', 'polished', 'failed'):
                if key in progress:
                    job[key] = progress[key]

        job['status'] = 'running'
        try:
            result = await run(update)
            # 查询进度只返回汇总，避免大量商品时反复传输逐件结果。
            summary = {key: result[key] for key in (
                'success', 'partial', 'list_complete', 'message', 'total', 'polished', 'failed',
            ) if key in result}
            job.update({
                'status': 'completed' if result.get('success') else 'failed',
                'stage': 'finished', 'message': result.get('message') or '擦亮任务结束',
                'result': summary,
            })
            for key in ('total', 'polished', 'failed'):
                job[key] = int(result.get(key) or 0)
            job['processed'] = job['polished'] + job['failed']
        except asyncio.CancelledError:
            job.update(status='cancelled', stage='finished', message='擦亮任务已中断，请查看任务日志')
            raise
        except Exception:
            job.update(status='failed', stage='finished', message='擦亮任务异常中断，请查看任务日志')
        finally:
            self._finish(job)
