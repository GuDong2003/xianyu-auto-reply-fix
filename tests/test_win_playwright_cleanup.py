"""Windows 下 Playwright 进程树兜底清理与同线程关闭回归测试。"""

import ast
import inspect
import os
import signal
import sys
import textwrap
import types
import unittest
from unittest import mock

from utils.xianyu_slider_stealth import XianyuSliderStealth


class WinPlaywrightCleanupTests(unittest.TestCase):
    def _make_stub(self):
        stub = object.__new__(XianyuSliderStealth)
        stub.pure_user_id = "test_user"
        stub._browser_pid = 4242
        return stub

    def test_force_kill_browser_process_tree_uses_taskkill_on_windows(self):
        stub = self._make_stub()
        proc_ok = mock.Mock(returncode=0)
        with mock.patch("utils.xianyu_slider_stealth.os.name", "nt"), \
             mock.patch("utils.xianyu_slider_stealth.subprocess.run", return_value=proc_ok) as mock_run:
            result = stub._force_kill_browser_process_tree("password_login_close_error")

        self.assertTrue(result)
        self.assertIsNone(stub._browser_pid)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd, ["taskkill", "/PID", "4242", "/T", "/F"])

    def _make_fake_psutil(self, root_proc, wait_procs_impl):
        class FakeNoSuchProcess(Exception):
            pass

        fake_psutil = types.SimpleNamespace(
            NoSuchProcess=FakeNoSuchProcess,
            Process=mock.Mock(return_value=root_proc),
            wait_procs=mock.Mock(side_effect=wait_procs_impl)
            if callable(wait_procs_impl) or isinstance(wait_procs_impl, list)
            else mock.Mock(return_value=wait_procs_impl),
        )
        return fake_psutil

    def test_force_kill_browser_process_tree_windows_nonzero_kills_descendants_and_clears_pid(self):
        stub = self._make_stub()
        proc_fail = mock.Mock(returncode=1)
        child_1 = mock.Mock(pid=5001)
        child_2 = mock.Mock(pid=5002)
        root_proc = mock.Mock(pid=4242)
        root_proc.children.return_value = [child_1, child_2]

        terminate_order = []
        child_1.terminate.side_effect = lambda: terminate_order.append(5001)
        child_2.terminate.side_effect = lambda: terminate_order.append(5002)
        root_proc.terminate.side_effect = lambda: terminate_order.append(4242)

        fake_psutil = self._make_fake_psutil(root_proc, ([child_2, child_1, root_proc], []))
        with mock.patch("utils.xianyu_slider_stealth.os.name", "nt"), \
             mock.patch("utils.xianyu_slider_stealth.subprocess.run", return_value=proc_fail), \
             mock.patch.dict(sys.modules, {"psutil": fake_psutil}):
            result = stub._force_kill_browser_process_tree("password_login_close_error")

        self.assertTrue(result)
        self.assertIsNone(stub._browser_pid)
        self.assertEqual(terminate_order, [5002, 5001, 4242])

    def test_force_kill_browser_process_tree_windows_nonzero_keeps_pid_when_descendant_alive(self):
        stub = self._make_stub()
        proc_fail = mock.Mock(returncode=1)
        child_alive = mock.Mock(pid=5001)
        child_alive.kill.side_effect = PermissionError("denied")
        root_proc = mock.Mock(pid=4242)
        root_proc.children.return_value = [child_alive]

        fake_psutil = self._make_fake_psutil(
            root_proc,
            [([root_proc], [child_alive]), ([], [child_alive])],
        )
        with mock.patch("utils.xianyu_slider_stealth.os.name", "nt"), \
             mock.patch("utils.xianyu_slider_stealth.subprocess.run", return_value=proc_fail), \
             mock.patch.dict(sys.modules, {"psutil": fake_psutil}):
            result = stub._force_kill_browser_process_tree("password_login_close_error")

        self.assertFalse(result)
        self.assertEqual(stub._browser_pid, 4242)
        child_alive.terminate.assert_called_once()
        child_alive.kill.assert_called_once()

    def test_force_kill_browser_process_tree_windows_nonzero_escalates_to_kill(self):
        stub = self._make_stub()
        proc_fail = mock.Mock(returncode=1)
        child_proc = mock.Mock(pid=5001)
        root_proc = mock.Mock(pid=4242)
        root_proc.children.return_value = [child_proc]

        fake_psutil = self._make_fake_psutil(
            root_proc,
            [([root_proc], [child_proc]), ([child_proc], [])],
        )
        with mock.patch("utils.xianyu_slider_stealth.os.name", "nt"), \
             mock.patch("utils.xianyu_slider_stealth.subprocess.run", return_value=proc_fail), \
             mock.patch.dict(sys.modules, {"psutil": fake_psutil}):
            result = stub._force_kill_browser_process_tree("password_login_close_error")

        self.assertTrue(result)
        self.assertIsNone(stub._browser_pid)
        child_proc.terminate.assert_called_once()
        child_proc.kill.assert_called_once()

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

    def test_password_login_does_not_spawn_pwd_login_close_thread(self):
        source = textwrap.dedent(inspect.getsource(XianyuSliderStealth.login_with_password_playwright))
        self.assertNotIn("pwd-login-close-", source)
        tree = ast.parse(source)
        # Ensure close()/stop() are not delegated to a new Thread target inside login_with_password_playwright
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_close_runtime_resources":
                self.fail("_close_runtime_resources cross-thread helper should not exist")


if __name__ == "__main__":
    unittest.main()
