"""Interactive human verification worker with an isolated loopback RFB server."""

from __future__ import annotations

import json
import os
import select
import socket
import subprocess  # nosec B404
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from xianyu_connector.infrastructure.account_lock import AccountProcessLock
from xianyu_connector.infrastructure.sqlite_secret_repository import SqliteSecretRepository
from xianyu_connector.infrastructure.verification_browser import (
    DEFAULT_VIEWPORT,
    MAX_FRAME_BYTES,
    EventSink,
    ManualVerificationSession,
    PageProtocol,
    VerificationInputError,
    launch_persistent_context,
    serialize_platform_cookies,
)
from xianyu_connector.security.aes_gcm import SecretCipher, load_master_key

DEFAULT_TTL_SECONDS = 600.0
DEFAULT_IDLE_SECONDS = 300.0
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
FRAME_INTERVAL_SECONDS = 0.5


class ManualVerificationWorker:
    """Run one isolated verification browser until operator completion."""

    def __init__(
        self,
        account_id: str,
        session_id: str,
        profiles_root: Path,
        *,
        event_sink: EventSink | None = None,
        stdin: Any | None = None,
        headless: bool | None = None,
        viewport: tuple[int, int] = DEFAULT_VIEWPORT,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        self.account_id = account_id
        self.session_id = session_id
        self._profiles_root = profiles_root
        self._event_sink = event_sink or _stdout_event_sink
        self._stdin = stdin or sys.stdin
        self._headless = _env_bool("XIANYU_VERIFICATION_HEADLESS", True) if headless is None else headless
        self._viewport = viewport
        self._ttl_seconds = ttl_seconds
        self._idle_seconds = idle_seconds
        self._max_frame_bytes = max_frame_bytes
        self._session: ManualVerificationSession | None = None
        self._context: Any = None
        self._rfb_process: subprocess.Popen[str] | None = None

    @property
    def profile_directory(self) -> Path:
        root = self._profiles_root.resolve()
        profile = (root / self.account_id / "chrome-profile").resolve()
        if profile != root and root not in profile.parents:
            raise ValueError("invalid account profile path")
        return profile

    def run(self) -> int:
        profile = self.profile_directory
        profile.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        last_activity = started
        self._emit_state("starting")
        try:
            with AccountProcessLock(
                profile,
                timeout_seconds=float(
                    os.getenv(
                        "XIANYU_VERIFICATION_LOCK_TIMEOUT_SECONDS",
                        DEFAULT_LOCK_TIMEOUT_SECONDS,
                    )
                ),
            ):
                self._emit_state("profile_lock_acquired")
                with launch_persistent_context(
                    profile, headless=self._headless, viewport=self._viewport
                ) as context:
                    self._context = context
                    page = context.pages[0] if context.pages else context.new_page()
                    self._session = ManualVerificationSession(
                        cast(PageProtocol, page),
                        account_id=self.account_id,
                        session_id=self.session_id,
                        event_sink=self._event_sink,
                        viewport=self._viewport,
                        max_frame_bytes=self._max_frame_bytes,
                    )
                    self._start_rfb_server()
                    self._emit_state("ready")
                    while time.monotonic() - started < self._ttl_seconds:
                        command = _read_command(self._stdin, 0.2)
                        now = time.monotonic()
                        if command is not None:
                            last_activity = now
                            result = self._handle_command(command)
                            if result is not None:
                                return result
                        if now - last_activity >= self._idle_seconds:
                            self._emit_state("expired", reason="operator_timeout")
                            return 20
                    self._emit_state("expired", reason="session_timeout")
                    return 20
        except BlockingIOError:
            self._emit_error("profile_locked")
            return 21
        except Exception as exc:
            self._emit_error(_safe_error_code(exc))
            return 30
        finally:
            self._stop_rfb_server()
            self._context = None

    def _handle_command(self, payload: Mapping[str, object]) -> int | None:
        command = str(payload.get("command") or "")
        if command == "open":
            if self._session is None:
                return 30
            try:
                self._session.open(str(payload.get("challenge_url") or ""))
            except Exception as exc:
                self._emit_error(_safe_error_code(exc))
                return 30
            return None
        if command == "input":
            if self._session is None:
                return 30
            try:
                self._session.handle_input(payload)
            except VerificationInputError:
                self._emit_error("invalid_input")
            except Exception as exc:
                self._emit_error(_safe_error_code(exc))
                return 30
            return None
        if command == "ping":
            self._event_sink(
                {"event": "heartbeat", "session_id": self.session_id, "ts": int(time.time())}
            )
            return None
        if command == "complete":
            try:
                self._persist_verified_cookies()
            except Exception:
                self._emit_error("cookie_persist_failed")
                return 30
            self._emit_state("complete_requested")
            self._event_sink({"event": "complete_requested", "session_id": self.session_id})
            return 0
        if command == "cancel":
            self._emit_state("cancelled")
            return 10
        self._emit_error("unsupported_command")
        return None

    def _emit_state(self, state: str, **extra: object) -> None:
        event: dict[str, object] = {
            "event": "state",
            "state": state,
            "session_id": self.session_id,
        }
        event.update(extra)
        self._event_sink(event)

    def _emit_error(self, code: str) -> None:
        self._event_sink(
            {
                "event": "error",
                "code": code,
                "session_id": self.session_id,
                "message": "人工验证浏览器未能继续，请重新发起验证",
            }
        )

    def _start_rfb_server(self) -> None:
        display = os.getenv("DISPLAY", "").strip()
        host = os.getenv("XIANYU_VERIFICATION_RFB_HOST", "127.0.0.1").strip()
        raw_port = os.getenv("XIANYU_VERIFICATION_RFB_PORT", "")
        if not display or not raw_port:
            raise RuntimeError("verification RFB runtime is unavailable")
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise RuntimeError("verification RFB port is invalid") from exc
        command = build_x11vnc_command(display, host, port)
        process = subprocess.Popen(  # nosec B603
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=False,
            env=os.environ.copy(),
        )
        if process.poll() is not None:
            raise RuntimeError("verification RFB server exited during startup")
        self._rfb_process = process
        _wait_for_rfb_listener(host, port, process)
        self._emit_state("rfb_ready", rfb_host=host, rfb_port=port)

    def _stop_rfb_server(self) -> None:
        process = self._rfb_process
        self._rfb_process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def _persist_verified_cookies(self) -> None:
        if self._context is None:
            raise RuntimeError("browser context is unavailable")
        database_path = Path(os.environ["DB_PATH"])
        master_key_path = Path(os.environ["XIANYU_MASTER_KEY_PATH"])
        cookie_header = serialize_platform_cookies(self._context.cookies())
        from db_manager import db_manager

        account_info = db_manager.get_cookie_details(self.account_id) or {}
        db_manager.update_cookie_account_info(
            self.account_id,
            cookie_value=cookie_header,
            user_id=account_info.get("user_id"),
        )
        repository = SqliteSecretRepository(
            database_path,
            SecretCipher(load_master_key(master_key_path)),
        )
        repository.save(self.account_id, "cookie", cookie_header)


def _read_command(stream: Any, timeout: float) -> dict[str, object] | None:
    try:
        ready, _, _ = select.select([stream], [], [], timeout)
    except (OSError, ValueError):
        return None
    if not ready:
        return None
    line = stream.readline()
    if not line:
        return {"command": "cancel"}
    try:
        payload = json.loads(line)
    except (TypeError, json.JSONDecodeError):
        return {"command": "invalid"}
    return dict(payload) if isinstance(payload, Mapping) else {"command": "invalid"}


def _stdout_event_sink(event: dict[str, object]) -> None:
    print(json.dumps(event, ensure_ascii=True, separators=(",", ":")), flush=True)


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return "invalid_challenge"
    if isinstance(exc, TimeoutError):
        return "browser_timeout"
    return "browser_unavailable"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


def build_x11vnc_command(display: str, host: str, port: int) -> list[str]:
    if not display.startswith(":"):
        raise ValueError("DISPLAY must be an X11 display")
    if host != "127.0.0.1":
        raise ValueError("verification RFB must use loopback")
    if not 1 <= port <= 65_535:
        raise ValueError("verification RFB port is invalid")
    return [
        os.getenv("XIANYU_X11VNC_EXECUTABLE", "x11vnc"),
        "-display",
        display,
        "-listen",
        host,
        "-localhost",
        "-rfbport",
        str(port),
        "-forever",
        "-nevershared",
        "-nopw",
        "-noclipboard",
        "-nosel",
        "-noprimary",
        "-input",
        "MB",
        "-quiet",
    ]


def _wait_for_rfb_listener(
    host: str,
    port: int,
    process: subprocess.Popen[str],
    *,
    timeout_seconds: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("verification RFB server exited before binding")
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError("verification RFB server did not bind")


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    worker = ManualVerificationWorker(
        sys.argv[1],
        sys.argv[2],
        Path(os.environ.get("XIANYU_PROFILES_ROOT", "/var/lib/xianyu/accounts")),
        ttl_seconds=float(os.getenv("XIANYU_VERIFICATION_TTL_SECONDS", DEFAULT_TTL_SECONDS)),
        idle_seconds=float(os.getenv("XIANYU_VERIFICATION_IDLE_SECONDS", DEFAULT_IDLE_SECONDS)),
        max_frame_bytes=int(os.getenv("XIANYU_VERIFICATION_FRAME_MAX_BYTES", MAX_FRAME_BYTES)),
    )
    return worker.run()


if __name__ == "__main__":
    raise SystemExit(main())
