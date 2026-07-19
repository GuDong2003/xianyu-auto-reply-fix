from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from xianyu_connector.browser_auth_worker import (
    AccountProcessStopTimeout,
    _account_lock,
    _browser_runtime_environment,
    _seed_persistent_profile,
    main,
)
from xianyu_connector.infrastructure.account_lock import (
    AccountProcessLock,
    ProfileLockedError,
    cleanup_stale_profile_singletons,
)


def test_browser_runtime_environment_uses_account_persistent_volume(tmp_path: Path) -> None:
    profile_directory = tmp_path / "account-1" / "chrome-profile"

    environment = _browser_runtime_environment(profile_directory)

    runtime_root = profile_directory.parent / "browser-runtime"
    assert environment == {
        "HOME": str(runtime_root / "home"),
        "XDG_CONFIG_HOME": str(runtime_root / "config"),
        "XDG_CACHE_HOME": str(runtime_root / "cache"),
        "XDG_RUNTIME_DIR": str(runtime_root / "runtime"),
    }
    for directory in environment.values():
        path = Path(directory)
        assert path.is_dir()
        assert path.stat().st_mode & 0o777 == 0o700


def test_seed_profile_launches_chromium_with_writable_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch = _LaunchRecorder()
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _Playwright(launch))
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "/opt/chromium")

    profile_directory = tmp_path / "account-1" / "chrome-profile"
    _seed_persistent_profile(profile_directory, {"cookie2": "secret-value"})

    assert launch.profile_directory == str(profile_directory)
    assert launch.options["executable_path"] == "/opt/chromium"
    assert launch.options["headless"] is True
    assert "--disable-crash-reporter" in launch.options["args"]
    assert "--disable-breakpad" in launch.options["args"]
    browser_environment = launch.options["env"]
    assert browser_environment["HOME"].startswith(str(profile_directory.parent))
    assert browser_environment["XDG_RUNTIME_DIR"].startswith(str(profile_directory.parent))
    assert os.environ.get("HOME") != browser_environment["HOME"]
    assert launch.context.closed is True


@pytest.mark.parametrize(
    "playwright_executable",
    [
        "/ms-playwright/chromium-1217/chrome-linux64/chrome",
        "/ms-playwright/chromium-1217/chrome-linux/chrome",
    ],
    ids=["amd64", "arm64"],
)
def test_seed_profile_uses_playwright_platform_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    playwright_executable: str,
) -> None:
    launch = _LaunchRecorder(executable_path=playwright_executable)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _Playwright(launch))
    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", raising=False)

    _seed_persistent_profile(tmp_path / "account-1" / "chrome-profile", {"cookie2": "value"})

    assert launch.options["executable_path"] == playwright_executable


def test_account_stop_timeout_is_reported_as_retryable_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["browser_auth_worker", "account-1", "7", "qr-1"])
    monkeypatch.setattr(
        "xianyu_connector.browser_auth_worker._authenticate",
        lambda *args: (_ for _ in ()).throw(
            AccountProcessStopTimeout("账号连接进程未在认证前退出，请重新尝试")
        ),
    )

    exit_code = main()
    event = json.loads(capsys.readouterr().out)

    assert exit_code == 30
    assert event == {
        "event": "error",
        "message": "账号连接进程未在认证前退出，请重新尝试",
    }


def test_account_lock_does_not_reclassify_body_blocking_io_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "xianyu_connector.browser_auth_worker.time.monotonic",
        lambda: 0.0,
    )

    with (
        pytest.raises(BlockingIOError, match="body failed"),
        _account_lock(tmp_path / "chrome-profile"),
    ):
        raise BlockingIOError("body failed")


def test_account_lock_waits_for_previous_worker_to_release(tmp_path: Path) -> None:
    profile_directory = tmp_path / "chrome-profile"
    acquired = threading.Event()

    with AccountProcessLock(profile_directory):
        def wait_for_lock() -> None:
            with AccountProcessLock(profile_directory, timeout_seconds=1.0):
                acquired.set()

        waiter = threading.Thread(target=wait_for_lock)
        waiter.start()
        time.sleep(0.05)
        assert not acquired.is_set()

    waiter.join(timeout=2)
    assert acquired.is_set()


def test_account_lock_timeout_does_not_block_later_retry(tmp_path: Path) -> None:
    profile_directory = tmp_path / "chrome-profile"

    with (
        AccountProcessLock(profile_directory),
        pytest.raises(BlockingIOError),
        AccountProcessLock(profile_directory, timeout_seconds=0.01),
    ):
        pass

    with AccountProcessLock(profile_directory, timeout_seconds=0.1):
        pass


def test_account_lock_closes_failed_attempt_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = []
    original_open = Path.open

    def recording_open(path: Path, *args: object, **kwargs: object):
        handle = original_open(path, *args, **kwargs)
        opened.append(handle)
        return handle

    attempts = 0

    def fake_flock(_fd: int, _flags: int) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise BlockingIOError

    monkeypatch.setattr(Path, "open", recording_open)
    monkeypatch.setattr("xianyu_connector.infrastructure.account_lock.fcntl.flock", fake_flock)

    with AccountProcessLock(tmp_path / "chrome-profile", timeout_seconds=0.1):
        assert len(opened) == 2
        assert opened[0].closed is True

    assert all(handle.closed for handle in opened)


def test_cleanup_stale_profile_singletons_removes_only_links(tmp_path: Path) -> None:
    profile_directory = tmp_path / "account-1" / "chrome-profile"
    profile_directory.mkdir(parents=True)
    (profile_directory / "SingletonLock").symlink_to("host-999999")
    (profile_directory / "SingletonCookie").symlink_to("cookie")
    (profile_directory / "SingletonSocket").symlink_to("socket")
    regular_file = profile_directory / "Preferences"
    regular_file.write_text("keep", encoding="utf-8")
    proc_root = tmp_path / "proc"
    proc_root.mkdir()

    cleanup_stale_profile_singletons(profile_directory, proc_root=proc_root)

    assert not (profile_directory / "SingletonLock").exists()
    assert not (profile_directory / "SingletonCookie").exists()
    assert not (profile_directory / "SingletonSocket").exists()
    assert regular_file.read_text(encoding="utf-8") == "keep"


def test_cleanup_profile_singletons_keeps_live_chromium_lock(tmp_path: Path) -> None:
    profile_directory = tmp_path / "account-1" / "chrome-profile"
    profile_directory.mkdir(parents=True)
    (profile_directory / "SingletonLock").symlink_to("host-4321")
    (profile_directory / "SingletonCookie").symlink_to("cookie")
    (profile_directory / "SingletonSocket").symlink_to("socket")
    proc_root = tmp_path / "proc" / "4321"
    proc_root.mkdir(parents=True)
    (proc_root / "cmdline").write_bytes(
        f"/ms-playwright/chrome --user-data-dir={profile_directory}\0".encode()
    )

    with pytest.raises(ProfileLockedError):
        cleanup_stale_profile_singletons(profile_directory, proc_root=tmp_path / "proc")

    assert (profile_directory / "SingletonLock").is_symlink()
    assert (profile_directory / "SingletonCookie").is_symlink()
    assert (profile_directory / "SingletonSocket").is_symlink()


class _Playwright:
    def __init__(self, chromium: object) -> None:
        self.chromium = chromium

    def __enter__(self) -> _Playwright:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _LaunchRecorder:
    def __init__(self, *, executable_path: str = "/unused") -> None:
        self.executable_path = executable_path
        self.profile_directory = ""
        self.options: dict[str, object] = {}
        self.context = _Context()

    def launch_persistent_context(self, profile_directory: str, **options: object) -> _Context:
        self.profile_directory = profile_directory
        self.options = options
        return self.context


class _Context:
    def __init__(self) -> None:
        self.pages = [_Page()]
        self.closed = False

    def add_cookies(self, cookies: list[dict[str, object]]) -> None:
        assert cookies[0]["name"] == "cookie2"

    def new_page(self) -> _Page:
        return _Page()

    def close(self) -> None:
        self.closed = True


class _Page:
    def goto(self, url: str, **options: object) -> None:
        assert url == "https://www.goofish.com/"
        assert options["timeout"] == 10_000
