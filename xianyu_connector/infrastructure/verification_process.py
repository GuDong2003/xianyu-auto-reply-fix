"""Process adapter for the human verification browser."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess  # nosec B404
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RfbEndpoint:
    host: str
    port: int


class RfbConnectionLease:
    def __init__(
        self,
        process: ManagedVerificationProcess,
        endpoint: RfbEndpoint,
        *,
        touch_callback: Callable[[], None] | None = None,
    ) -> None:
        self._process = process
        self.endpoint = endpoint
        self._touch_callback = touch_callback
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._process._release_rfb()

    def touch(self) -> None:
        if self._closed:
            return
        if self._process.touch_rfb() and self._touch_callback is not None:
            self._touch_callback()

    def __enter__(self) -> RfbConnectionLease:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class VerificationProcessConfig:
    profiles_root: Path
    python_executable: str = sys.executable


class ManagedVerificationProcess:
    def __init__(
        self,
        account_id: str,
        session_id: str,
        profile_directory: Path,
        process: subprocess.Popen[str],
        *,
        rfb_host: str = "127.0.0.1",
        rfb_port: int = 0,
    ) -> None:
        self.account_id = account_id
        self.session_id = session_id
        self.profile_directory = profile_directory
        self.process = process
        self._process_group_id = process.pid
        self.rfb_endpoint = RfbEndpoint(rfb_host, rfb_port)
        self._rfb_lock = threading.Lock()
        self._rfb_connected = False
        self._rfb_touch_lock = threading.Lock()
        self._last_rfb_touch = 0.0
        self._stdin_lock = threading.Lock()
        self._event_condition = threading.Condition()
        self._events: deque[dict[str, object]] = deque(maxlen=64)
        self._observed_states: set[str] = set()
        self._latest_frame: dict[str, object] | None = None
        self._latest_state: dict[str, object] | None = None
        self._latest_error: dict[str, object] | None = None
        self._completion: dict[str, object] | None = None
        self._rfb_ready = False
        self._reader_done = False
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    @property
    def pid(self) -> int:
        return self.process.pid

    def is_alive(self) -> bool:
        return self.process.poll() is None

    def acquire_rfb(
        self,
        *,
        touch_callback: Callable[[], None] | None = None,
    ) -> RfbConnectionLease:
        if not self.is_alive():
            raise RuntimeError("verification worker is not running")
        with self._rfb_lock:
            if self._rfb_connected:
                raise RuntimeError("RFB client is already connected")
            self._rfb_connected = True
        return RfbConnectionLease(
            self,
            self.rfb_endpoint,
            touch_callback=touch_callback,
        )

    def _release_rfb(self) -> None:
        with self._rfb_lock:
            self._rfb_connected = False

    def touch_rfb(self) -> bool:
        now = time.monotonic()
        with self._rfb_touch_lock:
            if now - self._last_rfb_touch < 1.0:
                return False
            self._last_rfb_touch = now
        self.ping()
        return True

    def send(self, command: dict[str, object]) -> None:
        stream = self.process.stdin
        if stream is None or not self.is_alive():
            raise RuntimeError("verification worker is not running")
        encoded = json.dumps(command, ensure_ascii=True, separators=(",", ":"))
        with self._stdin_lock:
            stream.write(encoded + "\n")
            stream.flush()

    def open(self, challenge_url: str) -> None:
        self.send({"command": "open", "challenge_url": challenge_url})

    def input(self, action: str, x: float, y: float) -> None:
        self.send({"command": "input", "action": action, "x": x, "y": y})

    def ping(self) -> None:
        self.send({"command": "ping"})

    def complete(self, *, timeout_seconds: float = 20.0) -> None:
        self.send({"command": "complete"})
        event = self.wait_for_completion(timeout_seconds)
        if event is None:
            raise RuntimeError("verification worker did not acknowledge completion")
        if event.get("event") == "error":
            raise RuntimeError("verification worker rejected completion")

    def cancel(self) -> None:
        self.send({"command": "cancel"})

    def get_frame(self) -> dict[str, object] | None:
        with self._event_condition:
            return dict(self._latest_frame) if self._latest_frame is not None else None

    def get_state(self) -> dict[str, object] | None:
        with self._event_condition:
            return dict(self._latest_state) if self._latest_state is not None else None

    def wait_for_state(self, state: str, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        with self._event_condition:
            while True:
                if state in self._observed_states:
                    return True
                if self._reader_done:
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._event_condition.wait(timeout=remaining)

    def wait_for_rfb_ready(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        with self._event_condition:
            while True:
                if self._rfb_ready:
                    return True
                if self._reader_done:
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._event_condition.wait(timeout=remaining)

    def get_error(self) -> dict[str, object] | None:
        with self._event_condition:
            return dict(self._latest_error) if self._latest_error is not None else None

    def consume_events(self, *, wait_seconds: float = 0.0) -> list[dict[str, object]]:
        with self._event_condition:
            if wait_seconds > 0 and not self._events and not self._reader_done:
                self._event_condition.wait(timeout=wait_seconds)
            events = list(self._events)
            self._events.clear()
            return events

    def wait_for_completion(self, timeout_seconds: float = 0.0) -> dict[str, object] | None:
        deadline = time.monotonic() + max(timeout_seconds, 0)
        with self._event_condition:
            while self._completion is None and not self._reader_done:
                remaining = deadline - time.monotonic()
                if timeout_seconds <= 0 or remaining <= 0:
                    break
                self._event_condition.wait(timeout=remaining)
            return dict(self._completion) if self._completion is not None else None

    def consume_completion(self) -> dict[str, object] | None:
        with self._event_condition:
            completion = self._completion
            self._completion = None
            return dict(completion) if completion is not None else None

    def stop(
        self,
        *,
        graceful_seconds: float = 2.0,
        grace_seconds: float = 10.0,
    ) -> None:
        if self.process.poll() is not None:
            _terminate_process_group(
                self.process,
                grace_seconds,
                process_group_id=self._process_group_id,
            )
            return
        try:
            self.cancel()
            self.process.wait(timeout=max(0.0, min(graceful_seconds, 2.0)))
        except (
            BrokenPipeError,
            OSError,
            RuntimeError,
            ValueError,
            subprocess.TimeoutExpired,
        ):
            pass
        _terminate_process_group(
            self.process,
            grace_seconds,
            process_group_id=self._process_group_id,
        )

    def _read_stdout(self) -> None:
        stream = self.process.stdout
        if stream is None:
            with self._event_condition:
                self._reader_done = True
                self._event_condition.notify_all()
            return
        try:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                with self._event_condition:
                    event_name = event.get("event")
                    if event_name == "frame":
                        self._latest_frame = event
                    else:
                        self._events.append(event)
                    if event_name == "state":
                        self._latest_state = event
                        state = str(event.get("state") or "")
                        if state:
                            self._observed_states.add(state)
                        if (
                            event.get("state") == "rfb_ready"
                            and event.get("rfb_host") == self.rfb_endpoint.host
                            and _as_int(event.get("rfb_port")) == self.rfb_endpoint.port
                        ):
                            self._rfb_ready = True
                    elif event_name == "error":
                        self._latest_error = event
                    if (
                        event_name == "complete_requested"
                        or (
                            event_name == "error"
                            and event.get("code")
                            not in {"invalid_input", "unsupported_command"}
                        )
                    ) or (
                        event_name == "state"
                        and event.get("state") in {"cancelled", "expired"}
                    ):
                        self._completion = event
                    self._event_condition.notify_all()
        finally:
            with self._event_condition:
                self._reader_done = True
                self._event_condition.notify_all()


class VerificationProcessSupervisor:
    def __init__(
        self,
        profiles_root: Path,
        *,
        python_executable: str = sys.executable,
        rfb_ready_timeout_seconds: float = 10.0,
        profile_lock_ready_timeout_seconds: float = 6.0,
    ) -> None:
        self._config = VerificationProcessConfig(profiles_root, python_executable)
        self._rfb_ready_timeout_seconds = rfb_ready_timeout_seconds
        self._profile_lock_ready_timeout_seconds = profile_lock_ready_timeout_seconds
        self._processes: dict[str, ManagedVerificationProcess] = {}
        self._lock = threading.Lock()

    def start(
        self,
        account_id: str,
        session_id: str,
        challenge_url: str,
    ) -> ManagedVerificationProcess:
        with self._lock:
            existing = self._processes.get(account_id)
            if existing and existing.is_alive():
                if existing.session_id != session_id:
                    raise RuntimeError("verification already active for this account")
                return existing
            if existing:
                existing.stop(grace_seconds=1)
                self._processes.pop(account_id, None)
            profile = self._profile_directory(account_id)
            rfb_port = _reserve_loopback_port()
            environment = os.environ.copy()
            environment["XIANYU_PROFILES_ROOT"] = str(self._config.profiles_root)
            environment["XIANYU_VERIFICATION_HEADLESS"] = "false"
            environment["XIANYU_VERIFICATION_RFB_HOST"] = "127.0.0.1"
            environment["XIANYU_VERIFICATION_RFB_PORT"] = str(rfb_port)
            command = [
                os.getenv("XIANYU_XVFB_RUN", "xvfb-run"),
                "-a",
                "-s",
                "-nolisten tcp -screen 0 1280x900x24",
                self._config.python_executable,
                "-m",
                "xianyu_connector.manual_verification_worker",
                account_id,
                session_id,
            ]
            process = subprocess.Popen(  # nosec B603
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
                env=environment,
            )
            managed = ManagedVerificationProcess(
                account_id,
                session_id,
                profile,
                process,
                rfb_host="127.0.0.1",
                rfb_port=rfb_port,
            )
            self._processes[account_id] = managed
            try:
                managed.open(challenge_url)
                if not managed.wait_for_state(
                    "profile_lock_acquired",
                    self._profile_lock_ready_timeout_seconds,
                ):
                    raise RuntimeError("verification profile lock did not become ready")
                if not managed.wait_for_rfb_ready(self._rfb_ready_timeout_seconds):
                    raise RuntimeError("verification RFB server did not become ready")
            except Exception:
                managed.stop(grace_seconds=1)
                self._processes.pop(account_id, None)
                raise
            return managed

    def get(self, account_id: str, session_id: str | None = None) -> ManagedVerificationProcess | None:
        with self._lock:
            process = self._processes.get(account_id)
            if process is None:
                return None
            if not process.is_alive():
                self._processes.pop(account_id, None)
                process.stop(grace_seconds=1)
                return None
            if session_id is not None and process.session_id != session_id:
                return None
            return process

    def stop(self, account_id: str, session_id: str | None = None) -> None:
        with self._lock:
            process = self._processes.get(account_id)
            if process is None or (session_id is not None and process.session_id != session_id):
                return
            process.stop()
            self._processes.pop(account_id, None)

    def stop_all(self) -> None:
        with self._lock:
            processes = tuple(self._processes.values())
            self._processes.clear()
        for process in processes:
            process.stop()

    def _profile_directory(self, account_id: str) -> Path:
        root = self._config.profiles_root.resolve()
        profile = (root / account_id / "chrome-profile").resolve()
        if profile != root and root not in profile.parents:
            raise ValueError("invalid account profile path")
        return profile


def _terminate_process_group(
    process: subprocess.Popen[str],
    grace_seconds: float,
    *,
    process_group_id: int | None = None,
) -> None:
    owned_group_id = process.pid if process_group_id is None else process_group_id
    if not _process_group_exists(owned_group_id):
        return
    with suppress(ProcessLookupError):
        os.killpg(owned_group_id, signal.SIGTERM)
    try:
        _wait_for_process_group_exit(owned_group_id, grace_seconds)
    except TimeoutError:
        with suppress(ProcessLookupError):
            os.killpg(owned_group_id, signal.SIGKILL)
        with suppress(TimeoutError, ProcessLookupError):
            _wait_for_process_group_exit(owned_group_id, grace_seconds)
    with suppress(TimeoutError, ProcessLookupError):
        process.wait(timeout=max(0.0, grace_seconds))
    with suppress(TimeoutError, ProcessLookupError):
        _wait_for_process_group_exit(owned_group_id, max(grace_seconds, 0.1))


def _wait_for_process_group_exit(process_group_id: int, timeout: float) -> None:
    deadline = time.monotonic() + max(timeout, 0.0)
    while _process_group_exists(process_group_id):
        if time.monotonic() >= deadline:
            raise TimeoutError("owned verification process group did not exit")
        time.sleep(0.05)


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _as_int(value: object) -> int | None:
    try:
        return int(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])
