import json
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright


class ItemPolishBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        if not Path(cls.playwright.chromium.executable_path).is_file():
            raise unittest.SkipTest('Install Playwright Chromium to run browser regressions')
        cls.browser = cls.playwright.chromium.launch(headless=True, args=['--no-sandbox'])
        cls.addClassCleanup(cls.browser.close)
        cls.script = (Path(__file__).resolve().parents[1] / 'static/js/app.js').read_text()

    def setUp(self):
        self.posts = 0
        self.polls = 0
        self.complete = False
        self.fail_next_poll = False
        self.deny_status = False
        self.errors = []
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.page.set_default_timeout(5000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.route('**/*', self.respond)
        self.page.goto('http://polish.test/')
        self.page.add_script_tag(content=self.script)
        self.page.evaluate('''() => {
            authToken = 'synthetic-browser-session';
            window.testToasts = [];
            window.testLoading = [];
            showToast = (message, tone) => testToasts.push({message, tone});
            toggleLoading = value => testLoading.push(value);
            const originalSetTimeout = window.setTimeout;
            window.setTimeout = (fn, ms, ...args) => originalSetTimeout(fn, Math.min(ms, 20), ...args);
        }''')

    def respond(self, route):
        request = route.request
        path = request.url.removeprefix('http://polish.test').split('?', 1)[0]
        if path == '/':
            route.fulfill(content_type='text/html', body='''
                <button id="polish" data-polish-account="test-account" onclick="polishAccountItems('test-account')">擦亮</button>
                <span id="progress" data-polish-progress="test-account"></span>
            ''')
            return
        if path == '/accounts/test-account/polish-items' and request.method == 'POST':
            self.posts += 1
            self.assertIn('background=true', request.url)
            data = {'success': True, 'accepted': True, 'job': {
                'job_id': 'synthetic-job', 'status': 'pending', 'message': '正在获取完整商品列表',
            }}
        elif path == '/accounts/test-account/polish-items/synthetic-job':
            self.polls += 1
            if self.fail_next_poll:
                self.fail_next_poll = False
                route.abort()
                return
            if self.deny_status:
                route.fulfill(status=403, content_type='application/json', body=json.dumps({'detail': '无权访问该擦亮任务'}))
                return
            data = {'success': True, 'job': {
                'job_id': 'synthetic-job', 'status': 'completed' if self.complete else 'running',
                'message': '全部 1001 件擦亮完成' if self.complete else '正在擦亮：500/1001',
                'result': {'success': True, 'partial': False, 'failed': 0} if self.complete else None,
            }}
        else:
            route.abort()
            return
        route.fulfill(content_type='application/json', body=json.dumps(data))

    def finish(self):
        self.complete = True
        self.page.wait_for_function("document.getElementById('progress').textContent === '全部 1001 件擦亮完成'")
        self.assertFalse(self.page.locator('#polish').is_disabled())
        self.assertEqual(self.posts, 1)
        self.assertEqual(self.page.evaluate('testToasts.at(-1).tone'), 'success')
        self.assertFalse(self.errors)

    def test_background_progress_does_not_keep_loading_overlay_or_repeat_post(self):
        self.page.click('#polish')
        self.page.wait_for_function("document.getElementById('progress').textContent.includes('500/1001')")
        self.assertFalse(self.page.evaluate('testLoading.at(-1)'))
        self.assertTrue(self.page.locator('#polish').is_disabled())
        self.page.evaluate("polishAccountItems('test-account')")
        self.assertEqual(self.posts, 1)
        self.finish()

    def test_temporary_status_network_failure_retries_query_without_restarting_work(self):
        self.fail_next_poll = True
        self.page.click('#polish')
        self.page.wait_for_function("document.getElementById('progress').textContent.includes('500/1001')")
        self.assertGreaterEqual(self.polls, 2)
        self.finish()

    def test_forbidden_status_stops_polling_without_starting_another_task(self):
        self.deny_status = True
        self.page.click('#polish')
        self.page.wait_for_function("itemPolishPolls.size === 0 && document.getElementById('progress').textContent.includes('无权访问')")
        self.assertFalse(self.page.locator('#polish').is_disabled())
        self.assertEqual(self.posts, 1)
        self.assertEqual(self.polls, 1)
        self.assertEqual(self.page.evaluate('testToasts.at(-1).tone'), 'warning')
        self.assertFalse(self.errors)


if __name__ == '__main__':
    unittest.main()
