from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path
from types import TracebackType
from typing import TextIO


class ProfileLockedError(BlockingIOError):
    """A live Chromium process still owns the persistent profile."""


class AccountProcessLock:
    def __init__(
        self,
        profile_directory: Path,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        self._path = profile_directory / "account.lock"
        self._timeout_seconds = timeout_seconds
        self._file: TextIO | None = None

    def __enter__(self) -> AccountProcessLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self._path.open("a+")
        deadline = (
            time.monotonic() + max(self._timeout_seconds, 0.0)
            if self._timeout_seconds is not None
            else None
        )
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                lock_file.close()
                if deadline is None:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(0.05, remaining))
                lock_file = self._path.open("a+")
        self._file = lock_file
        try:
            cleanup_stale_profile_singletons(self._path.parent)
        except BaseException:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            self._file = None
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._file:
            return
        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None


_CHROMIUM_SINGLETONS = ("SingletonLock", "SingletonCookie", "SingletonSocket")


def cleanup_stale_profile_singletons(
    profile_directory: Path,
    *,
    proc_root: Path = Path("/proc"),
) -> None:
    """Remove only stale Chromium singleton symlinks for one locked profile."""

    lock_path = profile_directory / "SingletonLock"
    if not lock_path.is_symlink():
        return
    target = _read_singleton_target(lock_path)
    pid = _parse_singleton_pid(target)
    if pid is not None and _process_uses_profile(proc_root, pid, profile_directory):
        raise ProfileLockedError("persistent profile is owned by a live Chromium process")
    for name in _CHROMIUM_SINGLETONS:
        _unlink_symlink(profile_directory / name)


def _read_singleton_target(lock_path: Path) -> str:
    try:
        return os.readlink(lock_path)
    except OSError:
        return ""


def _parse_singleton_pid(target: str) -> int | None:
    value = target.rsplit("-", 1)[-1]
    try:
        pid = int(value)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _process_uses_profile(proc_root: Path, pid: int, profile_directory: Path) -> bool:
    process_directory = proc_root / str(pid)
    if not process_directory.exists():
        return False
    cmdline_path = process_directory / "cmdline"
    try:
        command_line = cmdline_path.read_bytes().decode("utf-8", "replace")
    except OSError as exc:
        raise ProfileLockedError("unable to inspect the profile owner") from exc
    profile = str(profile_directory.resolve())
    lowered = command_line.lower()
    if profile not in command_line or "crashpad" in lowered:
        return False
    return "chrome" in lowered or "chromium" in lowered


def _unlink_symlink(path: Path) -> None:
    try:
        if path.is_symlink():
            path.unlink()
    except FileNotFoundError:
        return
