from pathlib import Path

import pytest

from xianyu_control.browser_runtime import (
    ProductionBrowserUnavailable,
    resolve_production_chromium_executable,
)


def test_resolve_production_chromium_uses_configured_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "chrome"
    executable.write_bytes(b"browser")
    executable.chmod(0o700)
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", str(executable))

    assert resolve_production_chromium_executable() == executable


def test_resolve_production_chromium_rejects_missing_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing-chrome"
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", str(missing))

    with pytest.raises(ProductionBrowserUnavailable):
        resolve_production_chromium_executable()
