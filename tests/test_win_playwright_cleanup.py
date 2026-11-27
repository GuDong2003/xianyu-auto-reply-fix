"""Windows 下 Playwright 进程树兜底清理与同线程关闭回归测试。"""

import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from contextlib import suppress
from unittest import mock

import psutil
from playwright.sync_api import sync_playwright

from utils import xianyu_slider_stealth as slider_module
from utils.playwright_cleanup import PlaywrightProcessTree
from utils.xianyu_slider_stealth import XianyuSliderStealth, _PasswordLoginRuntime


class WinPlaywrightCleanupTests(unittest.TestCase):
    def _make_stub(self):
        stub = object.__new__(XianyuSliderStealth)
        stub.pure_user_id = "test_user"
        stub._browser_pid = 4242
        return stub

    def test_windows_terminates_descendants_before_driver(self):
        stub = self._make_stub()
        root, child, grandchild = (mock.Mock(pid=pid) for pid in (4242, 5001, 5002))
        root.children.return_value = [child, grandchild]
        terminate_order = []
        for proc in (root, child, grandchild):
            proc.terminate.side_effect = lambda pid=proc.pid: terminate_order.append(pid)

        with mock.patch.object(slider_module.os, "name", "nt"), \
             mock.patch.object(psutil, "Process", return_value=root), \
             mock.patch.object(psutil, "wait_procs", return_value=([grandchild, child, root], [])):
            self.assertTrue(stub._force_kill_browser_process_tree("test"))

        self.assertIsNone(stub._browser_pid)
        self.assertEqual(terminate_order, [grandchild.pid, child.pid, root.pid])

    def test_windows_escalates_when_descendant_survives_terminate(self):
        stub = self._make_stub()
        root, child = mock.Mock(pid=4242), mock.Mock(pid=5001)
        root.children.return_value = [child]
        with mock.patch.object(slider_module.os, "name", "nt"), \
             mock.patch.object(psutil, "Process", return_value=root), \
             mock.patch.object(psutil, "wait_procs", side_effect=[([root], [child]), ([child], [])]):
            self.assertTrue(stub._force_kill_browser_process_tree("test"))

        self.assertIsNone(stub._browser_pid)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()

    def test_windows_retry_still_cleans_descendant_after_driver_exits(self):
        stub = self._make_stub()
        root = mock.Mock(pid=4242)
        child = mock.Mock(pid=5001)
        root.children.return_value = [child]
        live = {root.pid, child.pid}
        for proc in (root, child):
            proc.is_running.side_effect = lambda pid=proc.pid: pid in live
        root.terminate.side_effect = lambda: live.discard(root.pid)
        child.terminate.side_effect = child.kill.side_effect = psutil.AccessDenied(child.pid)

        def lookup_process(pid):
            if pid not in live:
                raise psutil.NoSuchProcess(pid)
            self.assertEqual(pid, root.pid)
            return root

        def wait_procs(procs, timeout):
            return ([p for p in procs if p.pid not in live], [p for p in procs if p.pid in live])

        with mock.patch.object(slider_module.os, "name", "nt"), \
             mock.patch.object(psutil, "Process", side_effect=lookup_process) as process, \
             mock.patch.object(psutil, "wait_procs", side_effect=wait_procs):
            self.assertFalse(stub._force_kill_browser_process_tree("first attempt"))
            self.assertEqual(live, {child.pid})
            self.assertEqual(stub._browser_pid, root.pid)

            child.terminate.side_effect = lambda: live.discard(child.pid)
            self.assertTrue(stub._force_kill_browser_process_tree("retry after driver exit"))
            self.assertFalse(live, "成功前必须实际清理由快照记录的 Chrome 子进程")
            self.assertIsNone(stub._browser_pid)
            process.assert_called_once_with(root.pid)

    def test_windows_missing_driver_without_snapshot_is_not_success(self):
        stub = self._make_stub()
        with mock.patch.object(slider_module.os, "name", "nt"), \
             mock.patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(stub._browser_pid)):
            self.assertFalse(stub._force_kill_browser_process_tree("missing driver"))
            self.assertEqual(stub._browser_pid, 4242)

    def test_password_login_returns_cookie_result_and_releases_slot_if_close_hangs(self):
        stub = self._make_stub()
        stub.browser_channel = "fixture"
        stub.executable_path = None
        stub._check_date_validity = mock.Mock(return_value=True)
        stub._get_random_browser_features = mock.Mock(
            return_value={"viewport_width": 800, "viewport_height": 600, "device_scale_factor": 1}
        )
        stub._build_playwright_proxy_settings = mock.Mock(return_value=None)
        stub._extract_browser_pid = mock.Mock(return_value=None)
        stub._apply_headless_network_fingerprint = mock.Mock()
        stub._get_password_login_selectors = mock.Mock()
        stub._find_login_form_with_retry = mock.Mock(return_value=(None, False, None))
        stub._release_concurrency_slot = mock.Mock()

        playwright, browser, context, page = (mock.Mock() for _ in range(4))
        context.new_page.return_value = page
        page.query_selector_all.return_value = []
        page.content.return_value = "offline fixture"
        stub._launch_clean_cookie_seeded_context = mock.Mock(return_value=(browser, context))
        stub._probe_context_login_success = mock.Mock(return_value=(True, page, None))
        expected = {"fixture": "offline-cookie"}
        stub._finalize_logged_in_cookies = mock.Mock(return_value=expected)
        owner_calls = []

        def start_playwright():
            owner_calls.append(("start", threading.get_ident()))
            return playwright

        stub._get_sync_playwright_factory = mock.Mock(
            return_value=mock.Mock(return_value=mock.Mock(start=start_playwright))
        )
        close_entered, unblock_close, close_finished, login_finished = (threading.Event() for _ in range(4))
        results = []

        def hang_close():
            owner_calls.append(("context.close", threading.get_ident()))
            close_entered.set()
            unblock_close.wait(10)

        def stop_playwright():
            owner_calls.append(("playwright.stop", threading.get_ident()))
            close_finished.set()

        context.close.side_effect = hang_close
        browser.close.side_effect = lambda: owner_calls.append(("browser.close", threading.get_ident()))
        playwright.stop.side_effect = stop_playwright

        def run_login():
            results.append(stub.login_with_password_playwright("fixture", "unused", True, force_clean_context=True))
            login_finished.set()

        caller = threading.Thread(target=run_login, daemon=True)
        with mock.patch.object(slider_module, "_PASSWORD_LOGIN_CLOSE_TIMEOUT", 0.05, create=True), \
             mock.patch.object(slider_module.time, "sleep"):
            caller.start()
            try:
                self.assertTrue(close_entered.wait(3), "fixture did not reach browser cleanup")
                self.assertTrue(login_finished.wait(2), "登录不应一直等待卡住的 close()")
                self.assertEqual(results, [expected])
                stub._release_concurrency_slot.assert_called_once()
                self.assertFalse(close_finished.is_set())
            finally:
                unblock_close.set()
                caller.join(3)
                close_finished.wait(3)

        self.assertEqual([action for action, _ in owner_calls], ["start", "context.close", "browser.close", "playwright.stop"])
        self.assertEqual(len({tid for _, tid in owner_calls}), 1, "Playwright 的所有操作必须留在创建线程")

    def test_force_kill_browser_process_tree_posix_handles_missing_sigkill(self):
        stub = self._make_stub()
        stub._collect_process_tree = mock.Mock(return_value=[4242, 4243])
        with mock.patch("utils.xianyu_slider_stealth.os.name", "posix"), \
             mock.patch("utils.xianyu_slider_stealth.os.kill") as mock_kill, \
             mock.patch("utils.xianyu_slider_stealth.time.sleep"), \
             mock.patch.object(signal, "SIGKILL", create=True, new=None):
            # Simulate an environment where signal.SIGKILL is absent by deleting it temporarily
            had_sigkill = hasattr(signal, "SIGKILL")
            saved_sigkill = getattr(signal, "SIGKILL", None)
            if had_sigkill:
                delattr(signal, "SIGKILL")
            try:
                result = stub._force_kill_browser_process_tree("posix_fallback")
            finally:
                if had_sigkill:
                    setattr(signal, "SIGKILL", saved_sigkill)

        self.assertTrue(result)
        self.assertIsNone(stub._browser_pid)
        killed_pids = [call.args for call in mock_kill.call_args_list]
        self.assertEqual(killed_pids, [(4242, signal.SIGTERM), (4243, signal.SIGTERM)])

    @unittest.skipUnless(sys.platform == "win32", "requires Windows process semantics")
    def test_windows_reaps_real_orphan_after_parent_exits(self):
        script = (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "time.sleep(60)"
        )
        with subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.DEVNULL) as parent:
            parent_process = psutil.Process(parent.pid)
            children = []
            try:
                deadline = time.monotonic() + 10
                while not children and time.monotonic() < deadline:
                    children = parent_process.children()
                    if not children:
                        time.sleep(0.05)
                self.assertEqual(len(children), 1)
                child = children[0]
                stub = self._make_stub()
                stub._browser_pid = parent.pid
                stub._get_windows_browser_process_tree(snapshot=True)

                parent.kill()
                parent.wait(timeout=10)
                self.assertTrue(child.is_running(), "fixture must leave the child alive")
                self.assertTrue(stub._force_kill_browser_process_tree("Windows orphan smoke test"))
                self.assertFalse(child.is_running())
            finally:
                if parent.poll() is None:
                    with suppress(psutil.NoSuchProcess):
                        children = parent_process.children(recursive=True)
                    parent.kill()
                    parent.wait(timeout=10)
                for proc in children:
                    with suppress(psutil.NoSuchProcess):
                        proc.kill()
                        proc.wait(timeout=5)

    @unittest.skipUnless(os.environ.get("XIANYU_PLAYWRIGHT_CLEANUP_SMOKE") == "1", "requires Playwright Chromium")
    def test_same_thread_cleanup_reaps_real_playwright_processes(self):
        stub = self._make_stub()
        runtime = _PasswordLoginRuntime(playwright=sync_playwright().start())
        try:
            pid = stub._extract_browser_pid(None, runtime.playwright)
            self.assertIsNotNone(pid, "must find the actual Playwright driver PID")
            driver = psutil.Process(pid)
            runtime.process_tree = PlaywrightProcessTree(pid, "smoke_test")
            runtime.browser = runtime.playwright.chromium.launch()
            runtime.context = runtime.browser.new_context()
            page = runtime.context.new_page()
            page.set_content("<title>offline fixture</title>")
            self.assertEqual(page.title(), "offline fixture")
            descendants = driver.children(recursive=True)
            self.assertTrue(descendants, "fixture must start Chromium descendants")
        finally:
            stub._close_password_login_runtime(runtime)

        _, alive = psutil.wait_procs([driver, *descendants], timeout=5)
        self.assertFalse(alive, f"driver or Chromium processes left alive: {alive}")


if __name__ == "__main__":
    unittest.main()
