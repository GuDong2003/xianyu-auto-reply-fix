import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from utils.node_runtime import configure_playwright_node


class NodeRuntimeTests(unittest.TestCase):
    def configure_version(self, version, env=None):
        executable = str(Path("node-test-executable").resolve())
        with mock.patch.dict(os.environ, env or {}, clear=True), \
             mock.patch("utils.node_runtime.shutil.which", return_value=executable), \
             mock.patch("utils.node_runtime.subprocess.run", return_value=mock.Mock(stdout=version)):
            selected = configure_playwright_node()
            self.assertEqual(selected, executable)
            self.assertEqual(os.environ["PLAYWRIGHT_NODEJS_PATH"], executable)

    def test_accepts_fixed_supported_release_lines(self):
        for version in ("v22.23.4", "v24.22.1", "v24.23.0", "v26.12.1"):
            with self.subTest(version=version):
                self.configure_version(version)

    def test_rejects_vulnerable_and_unsupported_release_lines(self):
        for version in ("v22.23.3", "v24.14.1", "v24.21.0", "v26.12.0", "v20.20.0", "v25.9.0"):
            with self.subTest(version=version), self.assertRaisesRegex(RuntimeError, "安全版本"):
                self.configure_version(version)

    def test_validates_explicit_override_instead_of_falling_back(self):
        explicit = str(Path("custom-node").resolve())
        with mock.patch.dict(os.environ, {"PLAYWRIGHT_NODEJS_PATH": explicit}, clear=True), \
             mock.patch("utils.node_runtime.shutil.which", return_value=explicit) as which, \
             mock.patch("utils.node_runtime.subprocess.run", return_value=mock.Mock(stdout="v24.21.0")):
            with self.assertRaisesRegex(RuntimeError, "安全版本"):
                configure_playwright_node()
            which.assert_called_once_with(explicit)

    def test_missing_node_fails_without_bundled_fallback(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("utils.node_runtime.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "未找到 Node.js"):
                configure_playwright_node()

    def test_failed_version_check_does_not_set_override(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("utils.node_runtime.shutil.which", return_value="node"), \
             mock.patch("utils.node_runtime.subprocess.run", side_effect=subprocess.TimeoutExpired("node", 10)):
            with self.assertRaisesRegex(RuntimeError, "无法校验"):
                configure_playwright_node()
            self.assertNotIn("PLAYWRIGHT_NODEJS_PATH", os.environ)

    def test_rejects_invalid_or_prerelease_version(self):
        for version in ("", "24.22.1", "v24.22.1-rc.1"):
            with self.subTest(version=version), self.assertRaisesRegex(RuntimeError, "无法识别"):
                self.configure_version(version)


if __name__ == "__main__":
    unittest.main()
