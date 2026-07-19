from __future__ import annotations

import asyncio
import base64
import io
import sqlite3
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from xianyu_connector import manual_verification_worker as worker_module
from xianyu_connector.api import create_connector_app
from xianyu_connector.application.account_operation_coordinator import (
    AccountOperationCoordinator,
    AccountOperationKind,
)
from xianyu_connector.application.runtime_service import RuntimeService
from xianyu_connector.application.verification_runtime import VerificationCoordinator
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.domain.commands import AccountCommand
from xianyu_connector.domain.verification_session import VerificationSessionState
from xianyu_connector.infrastructure import verification_process as process_module
from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_connector.infrastructure.sqlite_command_repository import (
    SqliteCommandRepository,
)
from xianyu_connector.infrastructure.sqlite_runtime_repository import (
    SqliteRuntimeRepository,
)
from xianyu_connector.infrastructure.sqlite_secret_repository import (
    SqliteSecretRepository,
)
from xianyu_connector.infrastructure.verification_process import (
    ManagedVerificationProcess,
    VerificationProcessSupervisor,
)
from xianyu_connector.infrastructure.verification_rfb import RfbWebSocketBridge
from xianyu_connector.security.aes_gcm import SecretCipher
from xianyu_connector.settings import ConnectorSettings

CHALLENGE_URL = "https://challenge.goofish.com/verify?secret=hidden"


class _FakePopen:
    pid = 4321

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(
            '{"event":"state","state":"profile_lock_acquired"}\n'
            '{"event":"state","state":"rfb_ready","rfb_host":"127.0.0.1",'
            '"rfb_port":59123,"session_id":"session-1"}\n'
        )

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            raise subprocess.TimeoutExpired("verification", 0)
        return self.returncode


def _settings(tmp_path: Path, *, remote_enabled: bool = True) -> ConnectorSettings:
    database_path = tmp_path / "api.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE cookies (id TEXT PRIMARY KEY, value TEXT, user_id INTEGER)"
        )
        connection.execute(
            "CREATE TABLE cookie_status (cookie_id TEXT PRIMARY KEY, enabled INTEGER)"
        )
        apply_connector_schema(connection)
    key_path = tmp_path / "master.key"
    key_path.write_bytes(base64.urlsafe_b64encode(b"k" * 32))
    return ConnectorSettings(
        database_path=database_path,
        profiles_root=tmp_path / "profiles",
        master_key_path=key_path,
        internal_api_token="t" * 32,
        remote_verification_enabled=remote_enabled,
    )


def _prepare_challenge(settings: ConnectorSettings) -> None:
    runtimes = SqliteRuntimeRepository(settings.database_path)
    RuntimeService(runtimes).transition_to(
        "account-1",
        AccountState.MANUAL_VERIFICATION_REQUIRED,
    )
    SqliteSecretRepository(
        settings.database_path,
        SecretCipher(b"k" * 32),
    ).save("account-1", "verification_url", CHALLENGE_URL)


def test_supervisor_launches_owned_headful_xvfb_and_loopback_rfb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(command: list[str], **options: object) -> _FakePopen:
        calls.append((command, options))
        return _FakePopen()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(process_module, "_reserve_loopback_port", lambda: 59123, raising=False)
    supervisor = VerificationProcessSupervisor(tmp_path, python_executable="python-test")

    handle = supervisor.start("account-1", "session-1", CHALLENGE_URL)

    command, options = calls[0]
    assert command[:2] == ["xvfb-run", "-a"]
    assert "-nolisten tcp" in " ".join(command)
    assert command[-4:] == [
        "-m",
        "xianyu_connector.manual_verification_worker",
        "account-1",
        "session-1",
    ]
    environment = options["env"]
    assert isinstance(environment, dict)
    assert environment["XIANYU_VERIFICATION_HEADLESS"] == "false"
    assert environment["XIANYU_VERIFICATION_RFB_HOST"] == "127.0.0.1"
    assert environment["XIANYU_VERIFICATION_RFB_PORT"] == "59123"
    assert options["start_new_session"] is True
    assert handle.rfb_endpoint.host == "127.0.0.1"
    assert handle.rfb_endpoint.port == 59123


@pytest.mark.asyncio
async def test_qr_login_clears_stale_verification_url(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _prepare_challenge(settings)
    secrets = SqliteSecretRepository(
        settings.database_path,
        SecretCipher(b"k" * 32),
    )
    coordinator = VerificationCoordinator(
        settings.database_path,
        settings.profiles_root,
        settings.master_key_path,
        SqliteRuntimeRepository(settings.database_path),
        SqliteCommandRepository(settings.database_path),
    )

    await coordinator.stop_for_qr("account-1")

    assert secrets.get("account-1", "verification_url") == ""


def test_manual_verification_reserves_account_until_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _prepare_challenge(settings)
    coordinator = AccountOperationCoordinator()

    class FakeVerificationProcess:
        session_id = "placeholder"

        def is_alive(self) -> bool:
            return True

        def stop(self, **kwargs: object) -> None:
            del kwargs

        def get_state(self) -> dict[str, str]:
            return {"state": "rfb_ready"}

        def get_error(self) -> None:
            return None

    process = FakeVerificationProcess()

    def fake_start(self, account_id: str, session_id: str, challenge_url: str):
        del challenge_url
        process.session_id = session_id
        self._processes[account_id] = process
        return process

    monkeypatch.setattr(VerificationProcessSupervisor, "start", fake_start)
    verification = VerificationCoordinator(
        settings.database_path,
        settings.profiles_root,
        settings.master_key_path,
        SqliteRuntimeRepository(settings.database_path),
        SqliteCommandRepository(settings.database_path),
        coordinator=coordinator,
    )

    created = verification.create("account-1", "7", "manual-reservation")
    assert coordinator.active_kind("account-1") is AccountOperationKind.MANUAL_VERIFICATION

    verification.cancel(
        "account-1",
        str(created["session_id"]),
        str(created["access_token"]),
    )
    assert coordinator.active_kind("account-1") is None


def test_x11vnc_command_is_single_connection_loopback_and_mouse_only() -> None:
    command = worker_module.build_x11vnc_command(":101", "127.0.0.1", 59123)

    assert command[0] == "x11vnc"
    assert command[command.index("-display") + 1] == ":101"
    assert "-localhost" in command
    assert "-forever" in command
    assert "-nevershared" in command
    assert "-nopw" in command
    assert "-noclipboard" in command
    assert "-nosel" in command
    assert "-noprimary" in command
    assert command[command.index("-input") + 1] == "MB"
    assert command[command.index("-rfbport") + 1] == "59123"
    assert "-shared" not in command


def test_rfb_lease_is_single_connection_and_released_on_disconnect(tmp_path: Path) -> None:
    handle = ManagedVerificationProcess(
        "account-1",
        "session-1",
        tmp_path / "chrome-profile",
        _FakePopen(),  # type: ignore[arg-type]
        rfb_host="127.0.0.1",
        rfb_port=59123,
    )

    lease = handle.acquire_rfb()
    with pytest.raises(RuntimeError, match="already connected"):
        handle.acquire_rfb()

    lease.close()
    replacement = handle.acquire_rfb()

    assert replacement.endpoint == lease.endpoint
    replacement.close()


def test_rfb_activity_sends_throttled_worker_ping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakePopen()
    handle = ManagedVerificationProcess(
        "account-1",
        "session-1",
        tmp_path / "chrome-profile",
        process,  # type: ignore[arg-type]
        rfb_host="127.0.0.1",
        rfb_port=59123,
    )
    times = iter((100.0, 100.5, 101.1))
    monkeypatch.setattr(process_module.time, "monotonic", lambda: next(times))
    lease = handle.acquire_rfb()

    lease.touch()
    lease.touch()
    lease.touch()

    assert process.stdin.getvalue().count('"command":"ping"') == 2
    lease.close()


def test_rfb_activity_touches_session_only_when_worker_ping_is_sent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakePopen()
    handle = ManagedVerificationProcess(
        "account-1",
        "session-1",
        tmp_path / "chrome-profile",
        process,  # type: ignore[arg-type]
        rfb_host="127.0.0.1",
        rfb_port=59123,
    )
    times = iter((100.0, 100.5, 101.1))
    monkeypatch.setattr(process_module.time, "monotonic", lambda: next(times))
    touches: list[str] = []
    lease = handle.acquire_rfb(touch_callback=lambda: touches.append("db"))

    lease.touch()
    lease.touch()
    lease.touch()

    assert touches == ["db", "db"]
    lease.close()


def test_stop_reaps_owned_process_group_even_if_leader_already_exited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakePopen()
    process.returncode = 20
    terminated: list[tuple[int, float]] = []
    monkeypatch.setattr(
        process_module,
        "_terminate_process_group",
        lambda candidate, grace, **kwargs: terminated.append((candidate.pid, grace)),
    )
    handle = ManagedVerificationProcess(
        "account-1",
        "session-1",
        tmp_path / "chrome-profile",
        process,  # type: ignore[arg-type]
        rfb_host="127.0.0.1",
        rfb_port=59123,
    )

    handle.stop(grace_seconds=3)

    assert terminated == [(4321, 3)]


def test_supervisor_reaps_dead_process_group_before_forgetting_session(
    tmp_path: Path,
) -> None:
    supervisor = VerificationProcessSupervisor(tmp_path)
    process = _FakePopen()
    process.returncode = 20
    handle = ManagedVerificationProcess(
        "account-1",
        "session-1",
        tmp_path / "account-1" / "chrome-profile",
        process,  # type: ignore[arg-type]
        rfb_host="127.0.0.1",
        rfb_port=59123,
    )
    stops: list[float] = []
    handle.stop = lambda **kwargs: stops.append(float(kwargs["grace_seconds"]))  # type: ignore[method-assign]
    supervisor._processes["account-1"] = handle

    assert supervisor.get("account-1", "session-1") is None
    assert stops == [1.0]


def test_process_group_cleanup_escalates_after_child_survives_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakePopen()
    process.returncode = 20
    group_states = iter((True, True, False, False))
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        process_module,
        "_process_group_exists",
        lambda process_group_id: next(group_states),
    )
    monkeypatch.setattr(
        process_module.os,
        "killpg",
        lambda process_group_id, signal_number: signals.append(
            (process_group_id, signal_number)
        ),
    )
    handle = ManagedVerificationProcess(
        "account-1",
        "session-1",
        Path("/profiles/account-1/chrome-profile"),
        process,  # type: ignore[arg-type]
        rfb_host="127.0.0.1",
        rfb_port=59123,
    )

    handle.stop(grace_seconds=0)

    assert signals == [(4321, process_module.signal.SIGTERM), (4321, process_module.signal.SIGKILL)]


def test_supervisor_waits_for_delayed_structured_rfb_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[float] = []

    class DelayedReadyStream:
        def __iter__(self):
            time.sleep(0.05)
            calls.append(time.monotonic())
            return iter(
                [
                    '{"event":"state","state":"profile_lock_acquired"}\n',
                    '{"event":"state","state":"rfb_ready","rfb_host":"127.0.0.1",'
                    '"rfb_port":59123}\n',
                    '{"event":"state","state":"ready"}\n',
                ]
            )

    class DelayedReadyProcess(_FakePopen):
        def __init__(self) -> None:
            super().__init__()
            self.stdout = DelayedReadyStream()

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: DelayedReadyProcess())
    monkeypatch.setattr(process_module, "_reserve_loopback_port", lambda: 59123)
    started = time.monotonic()
    VerificationProcessSupervisor(tmp_path).start("account-1", "session-1", CHALLENGE_URL)

    assert calls and calls[0] - started >= 0.04


def test_supervisor_splits_profile_lock_and_browser_startup_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DelayedPhaseStream:
        def __iter__(self):
            yield '{"event":"state","state":"starting"}\n'
            time.sleep(0.04)
            yield '{"event":"state","state":"profile_lock_acquired"}\n'
            time.sleep(0.04)
            yield (
                '{"event":"state","state":"rfb_ready",'
                '"rfb_host":"127.0.0.1","rfb_port":59123}\n'
            )

    class DelayedPhaseProcess(_FakePopen):
        def __init__(self) -> None:
            super().__init__()
            self.stdout = DelayedPhaseStream()

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: DelayedPhaseProcess())
    monkeypatch.setattr(process_module, "_reserve_loopback_port", lambda: 59123)

    VerificationProcessSupervisor(
        tmp_path,
        profile_lock_ready_timeout_seconds=0.06,
        rfb_ready_timeout_seconds=0.06,
    ).start("account-1", "session-1", CHALLENGE_URL)


def test_supervisor_ready_timeout_reaps_owned_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakePopen()
    process.stdout = io.StringIO("")
    terminated: list[int] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(process_module, "_reserve_loopback_port", lambda: 59123)
    monkeypatch.setattr(
        process_module,
        "_terminate_process_group",
        lambda candidate, grace, **kwargs: terminated.append(candidate.pid),
    )
    supervisor = VerificationProcessSupervisor(
        tmp_path,
        rfb_ready_timeout_seconds=0.01,
    )

    with pytest.raises(RuntimeError, match="did not become ready"):
        supervisor.start("account-1", "session-1", CHALLENGE_URL)

    assert terminated == [4321]
    assert supervisor.get("account-1", "session-1") is None


def test_coordinator_marks_session_failed_when_rfb_startup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _prepare_challenge(settings)
    operations = AccountOperationCoordinator()
    monkeypatch.setattr(
        VerificationProcessSupervisor,
        "start",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("rfb bind failed")),
    )
    coordinator = VerificationCoordinator(
        settings.database_path,
        settings.profiles_root,
        settings.master_key_path,
        SqliteRuntimeRepository(settings.database_path),
        SqliteCommandRepository(settings.database_path),
        coordinator=operations,
    )

    with pytest.raises(RuntimeError, match="验证浏览器启动失败"):
        coordinator.create("account-1", "7", "rfb-startup-failure")

    with sqlite3.connect(settings.database_path) as connection:
        failed_session_id, state, reason_code, archived_key = connection.execute(
            "SELECT session_id, state, reason_code, idempotency_key "
            "FROM account_verification_sessions WHERE reason_code = ?",
            ("browser_start_failed",),
        ).fetchone()
    assert state == "failed"
    assert reason_code == "browser_start_failed"
    assert archived_key == f"rfb-startup-failure:retired:{failed_session_id}"
    qr_lease = operations.reserve("account-1", AccountOperationKind.QR)
    operations.release(qr_lease)

    monkeypatch.setattr(coordinator._processes, "start", lambda *args, **kwargs: object())
    retried = coordinator.create("account-1", "7", "rfb-startup-failure")

    assert retried["session_id"] != failed_session_id
    assert retried["state"] == "waiting_for_operator"
    assert retried["access_token"]


def test_two_accounts_reserve_distinct_loopback_rfb_ports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports = iter((59123, 59124))

    def fake_popen(command: list[str], **options: object) -> _FakePopen:
        del command
        port = str(options["env"]["XIANYU_VERIFICATION_RFB_PORT"])
        process = _FakePopen()
        process.stdout = io.StringIO(
            '{"event":"state","state":"profile_lock_acquired"}\n'
            '{"event":"state","state":"rfb_ready","rfb_host":"127.0.0.1",'
            f'"rfb_port":{port}}}\n'
        )
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(process_module, "_reserve_loopback_port", lambda: next(ports))
    supervisor = VerificationProcessSupervisor(tmp_path)

    first = supervisor.start("account-1", "session-1", CHALLENGE_URL)
    second = supervisor.start("account-2", "session-2", CHALLENGE_URL)

    assert first.rfb_endpoint.port == 59123
    assert second.rfb_endpoint.port == 59124
    assert first.rfb_endpoint.port != second.rfb_endpoint.port
    supervisor.stop_all()


def test_rfb_bind_failure_is_not_reported_as_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    class LiveProcess:
        def poll(self) -> None:
            return None

    def reject_connection(*args: object, **kwargs: object):
        del args, kwargs
        raise OSError("bind refused")

    monkeypatch.setattr(worker_module.socket, "create_connection", reject_connection)

    with pytest.raises(TimeoutError):
        worker_module._wait_for_rfb_listener(
            "127.0.0.1",
            59123,
            LiveProcess(),  # type: ignore[arg-type]
            timeout_seconds=0.01,
        )


@pytest.mark.asyncio
async def test_rfb_bridge_relays_binary_frames_and_releases_lease(tmp_path: Path) -> None:
    class FakeWebSocket:
        def __init__(self) -> None:
            self.accepted = False
            self.sent: list[bytes] = []
            self.closed: tuple[int, str | None] | None = None
            self.messages = iter(
                (
                    {"type": "websocket.receive", "bytes": b"client-frame"},
                    {"type": "websocket.disconnect"},
                )
            )

        async def accept(self) -> None:
            self.accepted = True

        async def receive(self) -> dict[str, object]:
            return next(self.messages)

        async def send_bytes(self, data: bytes) -> None:
            self.sent.append(data)

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.closed = (code, reason)

    class FakeReader:
        def __init__(self) -> None:
            self.chunks = iter((b"server-frame", b""))

        async def read(self, size: int) -> bytes:
            del size
            return next(self.chunks)

    class FakeWriter:
        def __init__(self) -> None:
            self.writes: list[bytes] = []
            self.closed = False

        def write(self, data: bytes) -> None:
            self.writes.append(data)

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            return None

    worker_process = _FakePopen()
    process = ManagedVerificationProcess(
        "account-1",
        "session-1",
        tmp_path / "chrome-profile",
        worker_process,  # type: ignore[arg-type]
        rfb_host="127.0.0.1",
        rfb_port=59123,
    )
    lease = process.acquire_rfb()
    websocket = FakeWebSocket()
    reader = FakeReader()
    writer = FakeWriter()

    async def fake_open_connection(host: str, port: int):
        assert (host, port) == ("127.0.0.1", 59123)
        return reader, writer

    await RfbWebSocketBridge(open_connection=fake_open_connection).relay(websocket, lease)

    assert websocket.accepted is True
    assert writer.writes == [b"client-frame"]
    assert websocket.sent == [b"server-frame"]
    assert writer.closed is True
    assert '"command":"ping"' in worker_process.stdin.getvalue()
    replacement = process.acquire_rfb()
    replacement.close()


def test_coordinator_authorizes_operator_scope_and_enforces_one_rfb_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _prepare_challenge(settings)
    process = ManagedVerificationProcess(
        "account-1",
        "placeholder",
        settings.profiles_root / "account-1" / "chrome-profile",
        _FakePopen(),  # type: ignore[arg-type]
        rfb_host="127.0.0.1",
        rfb_port=59123,
    )

    def fake_start(self, account_id: str, session_id: str, challenge_url: str):
        del challenge_url
        process.session_id = session_id
        self._processes[account_id] = process
        return process

    monkeypatch.setattr(VerificationProcessSupervisor, "start", fake_start)
    runtimes = SqliteRuntimeRepository(settings.database_path)
    coordinator = VerificationCoordinator(
        settings.database_path,
        settings.profiles_root,
        settings.master_key_path,
        runtimes,
        SqliteCommandRepository(settings.database_path),
    )
    created = coordinator.create("account-1", "7", "rfb-scope-test")
    coordinator.get(
        "account-1",
        str(created["session_id"]),
        str(created["access_token"]),
        0,
    )

    with pytest.raises(PermissionError):
        coordinator.open_rfb("account-1", str(created["session_id"]), "8")
    with pytest.raises(LookupError):
        coordinator.open_rfb("account-2", str(created["session_id"]), "7")

    old_activity = datetime.now(UTC) - timedelta(seconds=10)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "UPDATE account_verification_sessions SET last_activity_at = ? "
            "WHERE session_id = ?",
            (old_activity.isoformat(), created["session_id"]),
        )
        connection.commit()

    lease = coordinator.open_rfb("account-1", str(created["session_id"]), "7")
    with pytest.raises(RuntimeError, match="already connected"):
        coordinator.open_rfb("account-1", str(created["session_id"]), "7")
    lease.touch()
    with sqlite3.connect(settings.database_path) as connection:
        updated_activity = datetime.fromisoformat(
            connection.execute(
                "SELECT last_activity_at FROM account_verification_sessions "
                "WHERE session_id = ?",
                (created["session_id"],),
            ).fetchone()[0]
        )
    assert updated_activity > old_activity
    assert '"command":"ping"' in process.process.stdin.getvalue()
    lease.close()
    coordinator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active_state", "expected_state"),
    [
        (VerificationSessionState.OPERATOR_ACTIVE, "cancelled"),
        (VerificationSessionState.SUBMITTED, "failed"),
        (VerificationSessionState.VERIFYING, "failed"),
    ],
)
async def test_qr_login_stops_active_verification_and_releases_profile_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    active_state: VerificationSessionState,
    expected_state: str,
) -> None:
    settings = _settings(tmp_path)
    _prepare_challenge(settings)

    class FakeVerificationProcess:
        pid = 4321
        session_id = "placeholder"
        stopped = False

        def is_alive(self) -> bool:
            return not self.stopped

        def stop(self, **kwargs: object) -> None:
            del kwargs
            self.stopped = True

        def get_state(self) -> dict[str, str]:
            return {"state": "rfb_ready"}

        def get_error(self) -> None:
            return None

    process = FakeVerificationProcess()

    def fake_start(self, account_id: str, session_id: str, challenge_url: str):
        del challenge_url
        process.session_id = session_id
        self._processes[account_id] = process
        return process

    monkeypatch.setattr(VerificationProcessSupervisor, "start", fake_start)
    coordinator = VerificationCoordinator(
        settings.database_path,
        settings.profiles_root,
        settings.master_key_path,
        SqliteRuntimeRepository(settings.database_path),
        SqliteCommandRepository(settings.database_path),
    )
    created = coordinator.create("account-1", "7", "active-rfb-before-qr")
    coordinator.get(
        "account-1",
        str(created["session_id"]),
        str(created["access_token"]),
        0,
    )

    if active_state is VerificationSessionState.SUBMITTED:
        coordinator._sessions.submit(str(created["session_id"]))
    elif active_state is VerificationSessionState.VERIFYING:
        coordinator._sessions.submit(str(created["session_id"]))
        coordinator._sessions.begin_verification(str(created["session_id"]))

    await coordinator.stop_for_qr("account-1")
    await coordinator.stop_for_qr("account-1")

    assert process.stopped is True
    assert coordinator._processes.get("account-1") is None
    with sqlite3.connect(settings.database_path) as connection:
        state = connection.execute(
            "SELECT state FROM account_verification_sessions WHERE session_id = ?",
            (created["session_id"],),
        ).fetchone()[0]
        reason_code = connection.execute(
            "SELECT reason_code FROM account_verification_sessions WHERE session_id = ?",
            (created["session_id"],),
        ).fetchone()[0]
    assert state == expected_state
    if expected_state == "failed":
        assert reason_code == "replaced_by_qr_login"


@pytest.mark.asyncio
async def test_qr_login_cancels_recovery_command_before_relogin_is_claimed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _prepare_challenge(settings)

    class FakeVerificationProcess:
        pid = 4321
        session_id = "placeholder"
        stopped = False

        def is_alive(self) -> bool:
            return not self.stopped

        def stop(self, **kwargs: object) -> None:
            del kwargs
            self.stopped = True

        def get_state(self) -> dict[str, str]:
            return {"state": "rfb_ready"}

        def get_error(self) -> None:
            return None

    process = FakeVerificationProcess()

    def fake_start(self, account_id: str, session_id: str, challenge_url: str):
        del challenge_url
        process.session_id = session_id
        self._processes[account_id] = process
        return process

    monkeypatch.setattr(VerificationProcessSupervisor, "start", fake_start)
    commands = SqliteCommandRepository(settings.database_path)
    runtimes = SqliteRuntimeRepository(settings.database_path)
    coordinator = VerificationCoordinator(
        settings.database_path,
        settings.profiles_root,
        settings.master_key_path,
        runtimes,
        commands,
    )
    created = coordinator.create("account-1", "7", "recovery-before-qr")
    session_id = str(created["session_id"])
    coordinator.get("account-1", session_id, str(created["access_token"]), 0)
    coordinator._sessions.submit(session_id)
    coordinator._sessions.begin_verification(session_id)
    RuntimeService(runtimes).transition_to("account-1", AccountState.AUTHENTICATING)
    coordinator._start_recovery("account-1", session_id)
    for _ in range(20):
        if session_id in coordinator._recovery_commands:
            break
        await asyncio.sleep(0)
    recovery_command_id = coordinator._recovery_commands[session_id]

    await coordinator.stop_for_qr("account-1")
    commands.enqueue("account-1", AccountCommand.RELOGIN_QR, "qr-after-recovery", {})

    assert session_id not in coordinator._recovery_tasks
    assert session_id not in coordinator._recovery_commands
    assert commands.get(recovery_command_id).status.value == "failed"
    claimed = commands.claim_next()
    assert claimed is not None
    assert claimed.command is AccountCommand.RELOGIN_QR


@pytest.mark.asyncio
async def test_qr_stop_rereads_session_after_concurrent_terminal_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _prepare_challenge(settings)

    class FakeVerificationProcess:
        pid = 4321
        session_id = "placeholder"
        stopped = False

        def is_alive(self) -> bool:
            return not self.stopped

        def stop(self, **kwargs: object) -> None:
            del kwargs
            self.stopped = True

        def get_state(self) -> dict[str, str]:
            return {"state": "rfb_ready"}

        def get_error(self) -> None:
            return None

    process = FakeVerificationProcess()

    def fake_start(self, account_id: str, session_id: str, challenge_url: str):
        del challenge_url
        process.session_id = session_id
        self._processes[account_id] = process
        return process

    monkeypatch.setattr(VerificationProcessSupervisor, "start", fake_start)
    coordinator = VerificationCoordinator(
        settings.database_path,
        settings.profiles_root,
        settings.master_key_path,
        SqliteRuntimeRepository(settings.database_path),
        SqliteCommandRepository(settings.database_path),
    )
    created = coordinator.create("account-1", "7", "concurrent-terminal-before-qr")
    session_id = str(created["session_id"])
    coordinator.get("account-1", session_id, str(created["access_token"]), 0)
    stop_started = threading.Event()
    allow_stop = threading.Event()
    original_stop = coordinator._processes.stop

    def delayed_stop(account_id: str, target_session_id: str | None = None) -> None:
        stop_started.set()
        assert allow_stop.wait(timeout=1)
        original_stop(account_id, target_session_id)

    monkeypatch.setattr(coordinator._processes, "stop", delayed_stop)
    stop_task = asyncio.create_task(coordinator.stop_for_qr("account-1"))
    assert await asyncio.to_thread(stop_started.wait, 1)
    coordinator._sessions.cancel(session_id)
    allow_stop.set()

    await stop_task

    assert coordinator._sessions.get(session_id).state is VerificationSessionState.CANCELLED
    assert process.stopped is True


def test_internal_rfb_websocket_rejects_invalid_connector_token(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with (
        TestClient(create_connector_app(settings)) as client,
        pytest.raises(WebSocketDisconnect) as rejected,
        client.websocket_connect(
            "/internal/accounts/account-1/verification-sessions/session-1/rfb",
            headers={
                "X-Connector-Token": "wrong",
                "X-Operator-Id": "7",
            },
        ),
    ):
        pass

    assert rejected.value.code == 4401


def test_remote_verification_is_disabled_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path, remote_enabled=False)
    _prepare_challenge(settings)

    with TestClient(create_connector_app(settings)) as client:
        response = client.post(
            "/internal/accounts/account-1/verification-sessions",
            headers={"X-Connector-Token": "t" * 32},
            json={"user_id": 7, "idempotency_key": "remote-disabled"},
        )

    assert response.status_code == 404


def test_verification_session_advertises_rfb_without_screenshot_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _prepare_challenge(settings)

    class FakeProcess:
        pid = 1234
        session_id = "session-1"

        def is_alive(self) -> bool:
            return True

        def get_frame(self) -> dict[str, object]:
            return {"event": "frame", "seq": 1, "image_base64": "must-not-be-primary"}

        def get_state(self) -> dict[str, object]:
            return {"state": "waiting_for_operator"}

        def get_error(self) -> None:
            return None

        def stop(self, **kwargs: object) -> None:
            del kwargs

    fake = FakeProcess()

    def fake_start(self, account_id: str, session_id: str, challenge_url: str):
        del challenge_url
        fake.session_id = session_id
        self._processes[account_id] = fake
        return fake

    monkeypatch.setattr(VerificationProcessSupervisor, "start", fake_start)
    headers = {"X-Connector-Token": "t" * 32}
    with TestClient(create_connector_app(settings)) as client:
        created = client.post(
            "/internal/accounts/account-1/verification-sessions",
            headers=headers,
            json={"user_id": 7, "idempotency_key": "rfb-transport-test"},
        )
        payload = created.json()
        activated = client.get(
            f"/internal/accounts/account-1/verification-sessions/{payload['session_id']}",
            headers={**headers, "X-Verification-Ticket": payload["access_token"]},
        )

    assert created.status_code == 200
    assert payload["transport"] == "rfb"
    assert payload["rfb_websocket_path"].endswith(f"/{payload['session_id']}/rfb")
    assert "frame" not in activated.json()
    assert activated.json()["frame_seq"] == 0


def test_production_image_bundles_novnc_and_never_publishes_vnc_ports() -> None:
    project_root = Path(__file__).resolve().parents[1]
    dockerfile = (project_root / "deploy/Dockerfile.production").read_text()
    compose = (project_root / "deploy/compose.production.yml").read_text()
    entrypoint = (project_root / "deploy/entrypoint.production.sh").read_text()

    assert "x11vnc" in dockerfile
    assert "ARG X11VNC_VERSION=0.9.16-9" in dockerfile
    assert "x11vnc=${X11VNC_VERSION}" in dockerfile
    assert "xvfb" in dockerfile
    assert "xauth" in dockerfile
    assert "novnc.SHA256SUMS" in dockerfile
    manifest = (project_root / "deploy/novnc.SHA256SUMS").read_text()
    source_manifest = (project_root / "deploy/novnc-source.SHA256SUMS").read_text()
    assert "# noVNC @novnc/novnc 1.7.0" in manifest
    assert "63107bd06d9e1f6136ff21aeda8cd62cbf0d433e" in manifest
    assert "README.md" not in manifest
    assert "README.md" in source_manifest
    dockerignore = (project_root / ".dockerignore").read_text()
    assert "!deploy/novnc.SHA256SUMS" in dockerignore
    apt_install = dockerfile.split("apt-get install", 1)[1].split(
        "playwright install-deps", 1
    )[0]
    assert "novnc" not in apt_install.lower()
    assert "/app/static/vendor/novnc" in dockerfile
    assert "EXPOSE 5900" not in dockerfile
    assert "EXPOSE 6080" not in dockerfile
    assert "5900:" not in compose
    assert "6080:" not in compose
    assert sum(
        line.strip().startswith("XIANYU_REMOTE_VERIFICATION_ENABLED:")
        for line in compose.splitlines()
    ) == 2
    assert "${XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN:-}" in compose
    assert "websockify" not in entrypoint
    assert "pkill" not in entrypoint
    assert "pkill" not in dockerfile
    workflow = (project_root / ".github/workflows/docker-image.yml").read_text()
    assert "file: deploy/Dockerfile.production" in workflow
    assert "file: ./Dockerfile" not in workflow
    quality_workflow = (project_root / ".github/workflows/connector-quality.yml").read_text()
    assert "sha256sum -c deploy/novnc.SHA256SUMS" in quality_workflow
    assert "sha256sum -c deploy/novnc-source.SHA256SUMS" in quality_workflow
    assert "ENABLE_VNC" in entrypoint
    assert "external production mode forbids legacy VNC" in entrypoint
