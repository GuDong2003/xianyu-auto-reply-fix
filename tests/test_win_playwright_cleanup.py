"""Windows 下 Playwright 进程树兜底清理与同线程关闭回归测试。"""

import ast
import inspect
import os
import signal
import textwrap
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
        with mock.patch("utils.xianyu_slider_stealth.os.name", "nt"), \
             mock.patch("utils.xianyu_slider_stealth.subprocess.run") as mock_run:
            result = stub._force_kill_browser_process_tree("password_login_close_error")

        self.assertTrue(result)
        self.assertIsNone(stub._browser_pid)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd, ["taskkill", "/PID", "4242", "/T", "/F"])

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
