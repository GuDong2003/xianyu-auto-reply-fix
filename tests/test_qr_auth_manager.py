from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from xianyu_connector.application.account_operation_coordinator import (
    AccountOperationCoordinator,
    AccountOperationKind,
)
from xianyu_connector.application.qr_auth_manager import (
    QrAuthManager,
    QrAuthSession,
    _public_status,
)
from xianyu_connector.application.runtime_reporter import RuntimeReporter
from xianyu_connector.application.runtime_service import RuntimeService
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.domain.commands import AccountCommand
from xianyu_connector.infrastructure.async_process import drain_stream, terminate_process
from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_connector.infrastructure.sqlite_command_repository import SqliteCommandRepository
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository


def _manager(
    tmp_path: Path,
    *,
    qr_generation_timeout_seconds: float = 30,
    stop_verification: Callable[[str], Awaitable[None] | None] | None = None,
    coordinator: AccountOperationCoordinator | None = None,
) -> tuple[QrAuthManager, SqliteCommandRepository, SqliteRuntimeRepository]:
    database_path = tmp_path / "qr.db"
    with sqlite3.connect(database_path) as connection:
        apply_connector_schema(connection)
    commands = SqliteCommandRepository(database_path)
    runtimes = SqliteRuntimeRepository(database_path)
    manager = QrAuthManager(
        database_path,
        tmp_path / "profiles",
        tmp_path / "master.key",
        commands,
        runtimes,
        qr_generation_timeout_seconds=qr_generation_timeout_seconds,
        stop_verification=stop_verification,
        coordinator=coordinator,
    )
    return manager, commands, runtimes


@pytest.mark.asyncio
async def test_qr_session_reserves_account_until_auth_process_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = AccountOperationCoordinator()
    manager, commands, _ = _manager(tmp_path, coordinator=coordinator)
    release_run = asyncio.Event()

    async def fake_run(session: QrAuthSession) -> None:
        session.qr_started_at = time.time()
        session.qr_code_url = "data:image/png;base64,AA=="
        session.state = "waiting"
        await release_run.wait()
        manager._release_operation(session)

    monkeypatch.setattr(manager, "_run", fake_run)
    created = await manager.create("account-1", 7)

    assert created["success"] is True
    assert coordinator.active_kind("account-1") is AccountOperationKind.QR
    stop = commands.claim_next()
    assert stop is not None

    release_run.set()
    await asyncio.gather(*manager._tasks.values())
    assert coordinator.active_kind("account-1") is None


def _mark_online(runtimes: SqliteRuntimeRepository, account_id: str) -> None:
    RuntimeService(runtimes).transition_to(account_id, AccountState.AUTHENTICATING)
    reporter = RuntimeReporter(account_id, runtimes)
    reporter.mark_session(True)
    reporter.mark_token(True)
    reporter.mark_websocket(True)
    reporter.mark_heartbeat()


def test_auth_event_waits_for_browser_exit_before_starting_connector(tmp_path: Path) -> None:
    manager, commands, _ = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7)

    manager._apply_event(session, {"event": "qr", "qr_code_url": "data:image/png;base64,AA=="})
    manager._apply_event(session, {"event": "status", "status": "scanned"})
    manager._apply_event(
        session,
        {"event": "authenticated", "account_info": {"account_id": "account-1"}},
    )

    payload = manager.payload(session)
    assert commands.claim_next() is None
    assert session.authenticated is True
    assert payload["success"] is True
    assert payload["status"] == "processing"


def test_unknown_auth_worker_event_does_not_mutate_session(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7, state="waiting")

    manager._apply_event(session, {"event": "heartbeat", "status": "ignored"})

    assert session.state == "waiting"
    assert session.message is None


def test_delayed_scan_still_receives_full_online_readiness_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _, _ = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7, started_at=100.0)
    monkeypatch.setattr("xianyu_connector.application.qr_auth_manager.time.time", lambda: 155.0)

    manager._apply_event(
        session,
        {"event": "authenticated", "account_info": {"account_id": "account-1"}},
    )

    assert session.authenticated_at == 155.0
    assert manager._online_deadline(session) == 245.0
    assert manager.payload(session)["expires_at"] == 190.0


def test_get_promotes_authenticated_session_only_after_runtime_online(tmp_path: Path) -> None:
    manager, _, runtimes = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7, authenticated=True)
    manager._sessions[session.session_id] = session

    assert manager.get("session-1", "other-account") is None

    _mark_online(runtimes, "account-1")
    payload = manager.get("session-1", "account-1")

    assert payload["status"] == "success"
    assert payload["message"] == "账号四项在线检查已全部通过"


@pytest.mark.parametrize("qr_state", ["waiting", "confirmed"])
def test_previous_manual_runtime_does_not_override_unauthenticated_qr_session(
    tmp_path: Path,
    qr_state: str,
) -> None:
    manager, _, runtimes = _manager(tmp_path)
    session = QrAuthSession(
        "session-1",
        "account-1",
        7,
        state=qr_state,
        qr_code_url="data:image/png;base64,AA==",
    )
    manager._sessions[session.session_id] = session
    RuntimeReporter("account-1", runtimes).require_manual_verification("risk", "verify")

    payload = manager.get("session-1", "account-1")

    assert payload["status"] == qr_state
    assert session.state == qr_state
    assert session.message is None


def test_current_authenticated_session_can_surface_manual_runtime(tmp_path: Path) -> None:
    manager, _, runtimes = _manager(tmp_path)
    session = QrAuthSession(
        "session-1",
        "account-1",
        7,
        state="processing",
        authenticated=True,
        authenticated_at=155.0,
    )
    manager._sessions[session.session_id] = session
    RuntimeReporter("account-1", runtimes).require_manual_verification("risk", "verify")

    payload = manager.get("session-1", "account-1")

    assert payload["status"] == "verification_required"
    assert payload["message"] == "verify"


@pytest.mark.asyncio
async def test_create_reuses_active_session_for_same_account(tmp_path: Path, monkeypatch) -> None:
    manager, commands, _ = _manager(tmp_path)

    async def fake_run(session: QrAuthSession) -> None:
        session.qr_code_url = "data:image/png;base64,AA=="
        session.state = "waiting"

    monkeypatch.setattr(manager, "_run", fake_run)

    first = await manager.create("account-1", 7)
    second = await manager.create("account-1", 7)

    assert first["session_id"] == second["session_id"]
    assert commands.claim_next().command is AccountCommand.RELOGIN_QR
    assert commands.claim_next() is None
    await manager.close()


@pytest.mark.asyncio
async def test_create_reports_verification_stop_failure_without_reserving_or_enqueuing(
    tmp_path: Path,
) -> None:
    coordinator = AccountOperationCoordinator()

    async def fail_stop_verification(account_id: str) -> None:
        assert account_id == "account-1"
        raise RuntimeError("verification process did not stop")

    manager, commands, _ = _manager(
        tmp_path,
        stop_verification=fail_stop_verification,
        coordinator=coordinator,
    )

    result = await manager.create("account-1", 7)

    assert result["status"] == "error"
    assert result["message"] == "验证浏览器停止失败，请重新尝试"
    assert commands.claim_next() is None
    assert coordinator.active_kind("account-1") is None
    assert manager._tasks == {}


@pytest.mark.asyncio
async def test_create_preserves_existing_operation_when_qr_lease_conflicts(tmp_path: Path) -> None:
    coordinator = AccountOperationCoordinator()
    existing = coordinator.reserve("account-1", AccountOperationKind.MANUAL_VERIFICATION)
    manager, commands, _ = _manager(tmp_path, coordinator=coordinator)

    result = await manager.create("account-1", 7)

    assert result["status"] == "error"
    assert result["message"] == "账号正在执行其他操作，请稍后重试"
    assert commands.claim_next() is None
    assert coordinator.active_kind("account-1") is AccountOperationKind.MANUAL_VERIFICATION
    assert manager._tasks == {}
    coordinator.release(existing)


@pytest.mark.asyncio
async def test_create_stops_verification_then_waits_for_relogin_before_auth_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    manager, commands, _ = _manager(
        tmp_path,
        stop_verification=lambda account_id: events.append(
            f"verification_stopped:{account_id}"
        ),
    )
    process = _FakeProcess([])
    finish_process = asyncio.Event()

    async def fake_create_subprocess(*args: object, **kwargs: object) -> _FakeProcess:
        del args, kwargs
        events.append("auth_worker_started")
        return process

    async def fake_consume_events(
        session: QrAuthSession,
        candidate: _FakeProcess,
    ) -> None:
        assert candidate is process
        manager._apply_event(
            session,
            {"event": "qr", "qr_code_url": "data:image/png;base64,AA=="},
        )
        await finish_process.wait()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)
    monkeypatch.setattr(manager, "_consume_events", fake_consume_events)
    create_task = asyncio.create_task(manager.create("account-1", 7))

    stop_command = None
    for _ in range(50):
        stop_command = commands.claim_next()
        if stop_command is not None:
            break
        await asyncio.sleep(0.01)
    assert stop_command is not None
    assert events == ["verification_stopped:account-1"]

    commands.complete(stop_command.command_id, result={"state": "qr_pending"})
    created = await create_task

    assert created["success"] is True
    assert events == ["verification_stopped:account-1", "auth_worker_started"]
    finish_process.set()
    await asyncio.gather(*manager._tasks.values())
    await manager.close()


@pytest.mark.asyncio
async def test_qr_generation_window_starts_after_account_stop(
    tmp_path: Path,
) -> None:
    manager, _, _ = _manager(tmp_path)
    now = time.time()
    session = QrAuthSession(
        "session-1",
        "account-1",
        7,
        started_at=now - 40,
        qr_started_at=now - 1,
    )

    async def publish_qr() -> None:
        await asyncio.sleep(0.01)
        session.qr_code_url = "data:image/png;base64,AA=="

    publisher = asyncio.create_task(publish_qr())
    await manager._await_qr_or_fail(session)
    await publisher

    assert session.qr_code_url is not None
    assert session.state == "pending"
    assert manager.payload(session)["expires_at"] == session.qr_started_at + 90


@pytest.mark.asyncio
async def test_qr_generation_timeout_terminates_registered_process_and_releases_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = AccountOperationCoordinator()
    manager, _, _ = _manager(
        tmp_path,
        qr_generation_timeout_seconds=0,
        coordinator=coordinator,
    )
    session = QrAuthSession(
        "session-1",
        "account-1",
        7,
        qr_started_at=time.time() - 1,
        operation_lease=coordinator.reserve("account-1", AccountOperationKind.QR),
    )
    process = _FakeProcess([])
    manager._processes[session.session_id] = process
    terminated: list[object] = []

    async def record_termination(candidate: object) -> None:
        terminated.append(candidate)

    monkeypatch.setattr(
        "xianyu_connector.application.qr_auth_session.terminate_process",
        record_termination,
    )

    await manager._await_qr_or_fail(session)

    assert terminated == [process]
    assert session.state == "error"
    assert session.message == "二维码生成超时，请重新尝试"
    assert coordinator.active_kind("account-1") is None


@pytest.mark.asyncio
async def test_qr_generation_timeout_terminates_pending_session_and_allows_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager, _, _ = _manager(tmp_path, qr_generation_timeout_seconds=0.01)
    blocked = asyncio.Event()

    async def stuck_run(session: QrAuthSession) -> None:
        session.qr_started_at = time.time()
        await blocked.wait()

    monkeypatch.setattr(manager, "_run", stuck_run)
    failed = await manager.create("account-1", 7)

    assert failed["success"] is False
    assert failed["status"] == "error"
    assert failed["qr_code_url"] is None
    assert failed["message"] == "二维码生成超时，请重新尝试"
    assert manager._active_for_account("account-1") is None
    assert manager._tasks[failed["session_id"]].done()

    async def successful_run(session: QrAuthSession) -> None:
        session.qr_code_url = "data:image/png;base64,AA=="
        session.state = "waiting"

    monkeypatch.setattr(manager, "_run", successful_run)
    retried = await manager.create("account-1", 7)

    assert retried["success"] is True
    assert retried["session_id"] != failed["session_id"]
    await manager.close()


@pytest.mark.asyncio
async def test_qr_error_converges_qr_pending_runtime_to_offline(tmp_path: Path) -> None:
    manager, _, runtimes = _manager(tmp_path)
    RuntimeService(runtimes).transition_to("account-1", AccountState.QR_PENDING)
    session = QrAuthSession("session-1", "account-1", 7, state="error")
    session.message = "二维码生成失败，请重新尝试"

    await manager._converge_failed_runtime(session)

    runtime = runtimes.get("account-1")
    assert runtime.state is AccountState.OFFLINE
    assert runtime.reason_code == "qr_auth_failed"
    assert runtime.reason_message == "二维码生成失败，请重新尝试"


@pytest.mark.asyncio
async def test_qr_failure_convergence_accepts_already_offline_runtime(tmp_path: Path) -> None:
    manager, _, runtimes = _manager(tmp_path)
    service = RuntimeService(runtimes)
    service.transition_to("account-1", AccountState.QR_PENDING)
    service.transition_to("account-1", AccountState.OFFLINE)
    session = QrAuthSession("session-1", "account-1", 7, state="expired")

    await manager._converge_failed_runtime(session)

    runtime = runtimes.get("account-1")
    assert runtime.state is AccountState.OFFLINE
    assert session.message == "二维码已过期，请重新尝试"


@pytest.mark.asyncio
async def test_qr_failure_convergence_waits_for_active_runtime_to_stop(
    tmp_path: Path,
) -> None:
    manager, _, runtimes = _manager(tmp_path)
    service = RuntimeService(runtimes)
    service.transition_to("account-1", AccountState.AUTHENTICATING)
    session = QrAuthSession("session-1", "account-1", 7, state="error")
    session.message = "unexpected internal detail"

    convergence = asyncio.create_task(manager._converge_failed_runtime(session))
    await asyncio.sleep(0.02)
    service.transition_to("account-1", AccountState.OFFLINE)
    await convergence

    assert runtimes.get("account-1").state is AccountState.OFFLINE
    assert session.message == "二维码生成失败，请重新尝试"


def test_manager_recovers_orphaned_qr_pending_state_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "qr.db"
    with sqlite3.connect(database_path) as connection:
        apply_connector_schema(connection)
    commands = SqliteCommandRepository(database_path)
    runtimes = SqliteRuntimeRepository(database_path)
    RuntimeService(runtimes).transition_to("account-1", AccountState.QR_PENDING)
    service = RuntimeService(runtimes)
    service.transition_to("account-2", AccountState.QR_PENDING)
    service.transition_to("account-2", AccountState.OFFLINE)

    QrAuthManager(
        database_path,
        tmp_path / "profiles",
        tmp_path / "master.key",
        commands,
        runtimes,
    )

    runtime = runtimes.get("account-1")
    assert runtime.state is AccountState.OFFLINE
    assert runtime.reason_code == "qr_session_lost"
    assert runtimes.get("account-2").state is AccountState.OFFLINE


@pytest.mark.asyncio
async def test_timeout_stops_account_and_requires_qr_regeneration(tmp_path: Path) -> None:
    manager, commands, runtimes = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7, authenticated=True)
    RuntimeService(runtimes).transition_to("account-1", AccountState.QR_PENDING)

    async def complete_stop_command() -> None:
        while not (record := commands.claim_next()):
            await asyncio.sleep(0)
        RuntimeService(runtimes).transition_to(
            "account-1",
            AccountState.OFFLINE,
            clear_readiness=True,
        )
        commands.complete(record.command_id, result={"state": "offline"})

    completion = asyncio.create_task(complete_stop_command())
    await manager._stop_after_timeout(session)
    await completion

    runtime = runtimes.get("account-1")
    assert runtime.state is AccountState.OFFLINE
    assert runtime.reason_code == "authentication_timeout"
    assert session.state == "error"
    assert session.message == "账号登录校验超时，请重新生成二维码"


@pytest.mark.asyncio
async def test_timeout_stop_failure_does_not_publish_manual_verification(tmp_path: Path) -> None:
    manager, commands, runtimes = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7, authenticated=True)
    RuntimeService(runtimes).transition_to("account-1", AccountState.QR_PENDING)

    async def fail_stop_command() -> None:
        while not (record := commands.claim_next()):
            await asyncio.sleep(0)
        commands.complete(record.command_id, error_message="stop failed")

    failure = asyncio.create_task(fail_stop_command())
    await manager._stop_after_timeout(session)
    await failure

    runtime = runtimes.get("account-1")
    assert runtime.state is not AccountState.MANUAL_VERIFICATION_REQUIRED
    assert session.state == "error"
    assert session.message == "账号连接进程停止失败，请重新尝试"


@pytest.mark.asyncio
async def test_timeout_cancels_pending_stop_as_retryable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, commands, _ = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7, authenticated=True)

    async def skip_poll_delay(_delay: float) -> None:
        return None

    monkeypatch.setattr(
        "xianyu_connector.application.qr_auth_manager.asyncio.sleep",
        skip_poll_delay,
    )

    await manager._stop_after_timeout(session)

    pending = commands.claim_next()
    assert pending is not None
    assert pending.command is AccountCommand.STOP
    assert session.state == "error"
    assert session.message == "账号连接进程停止超时，请重新尝试"


@pytest.mark.asyncio
async def test_timeout_handles_runtime_disappearing_after_confirmed_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, commands, runtimes = _manager(tmp_path)
    service = RuntimeService(runtimes)
    service.transition_to("account-1", AccountState.QR_PENDING)
    service.transition_to("account-1", AccountState.OFFLINE, clear_readiness=True)
    offline = runtimes.get("account-1")
    session = QrAuthSession("session-1", "account-1", 7, authenticated=True)
    original_enqueue = commands.enqueue

    def enqueue_completed_stop(*args, **kwargs):
        command = original_enqueue(*args, **kwargs)
        claimed = commands.claim_next()
        assert claimed is not None
        assert claimed.command_id == command.command_id
        commands.complete(claimed.command_id, result={"state": "offline"})
        return command

    observations = iter((offline, None))
    monkeypatch.setattr(commands, "enqueue", enqueue_completed_stop)
    monkeypatch.setattr(runtimes, "get", lambda _account_id: next(observations))

    await manager._stop_after_timeout(session)

    assert session.state == "error"
    assert session.message == "账号登录校验超时，请重新生成二维码"


@pytest.mark.asyncio
async def test_online_wait_timeout_delegates_to_account_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _, _ = _manager(tmp_path)
    session = QrAuthSession(
        "session-1",
        "account-1",
        7,
        state="processing",
        authenticated=True,
        authenticated_at=time.time() - 100,
    )
    stopped: list[str] = []

    async def record_stop(target: QrAuthSession) -> None:
        stopped.append(target.session_id)

    monkeypatch.setattr(manager, "_stop_after_timeout", record_stop)

    await manager._wait_until_online(session)

    assert stopped == ["session-1"]


def test_authenticated_session_ignores_nonterminal_runtime_state(tmp_path: Path) -> None:
    manager, _, runtimes = _manager(tmp_path)
    RuntimeService(runtimes).transition_to("account-1", AccountState.QR_PENDING)
    session = QrAuthSession(
        "session-1",
        "account-1",
        7,
        state="processing",
        authenticated=True,
    )

    manager._refresh_online_state(session)

    assert session.state == "processing"
    assert session.message is None


@pytest.mark.asyncio
async def test_event_consumer_failure_terminates_qr_process_before_returning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _, _ = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7)
    exited = asyncio.Event()
    terminated = asyncio.Event()

    class BlockingProcess:
        pid = 4242
        returncode: int | None = None
        stdout = _FakeStream([])
        stderr = _FakeStream([])

        async def wait(self) -> int:
            await exited.wait()
            assert self.returncode is not None
            return self.returncode

    process = BlockingProcess()

    async def fake_create_subprocess(*args: object, **kwargs: object) -> BlockingProcess:
        del args, kwargs
        return process

    async def fail_consume(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise ValueError("invalid worker event")

    async def fake_terminate(candidate: BlockingProcess) -> None:
        assert candidate is process
        terminated.set()
        candidate.returncode = 30
        exited.set()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)
    monkeypatch.setattr(manager, "_consume_events", fail_consume)
    monkeypatch.setattr(
        "xianyu_connector.application.qr_auth_manager.terminate_process",
        fake_terminate,
    )

    await asyncio.wait_for(manager._run(session), timeout=0.2)

    assert terminated.is_set()
    assert session.state == "error"


@pytest.mark.asyncio
async def test_run_consumes_process_events_and_waits_for_online(
    tmp_path: Path, monkeypatch
) -> None:
    manager, commands, _ = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7)
    events = [
        {"event": "qr", "qr_code_url": "data:image/png;base64,AA=="},
        {"event": "authenticated", "account_info": {"account_id": "account-1"}},
    ]
    process = _FakeProcess([json.dumps(event).encode() + b"\n" for event in events])

    async def fake_create_subprocess(*args, **kwargs):
        return process

    async def fake_wait_until_online(target: QrAuthSession) -> None:
        target.state = "success"

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)
    monkeypatch.setattr(manager, "_wait_until_online", fake_wait_until_online)

    await manager._run(session)

    assert session.state == "success"
    assert session.authenticated is True
    assert manager._processes == {}
    assert commands.claim_next().command is AccountCommand.START


@pytest.mark.asyncio
async def test_run_waits_for_account_stop_before_starting_auth_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, commands, _ = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7)
    stop = commands.enqueue("account-1", AccountCommand.RELOGIN_QR, "qr-stop-1", {})
    session.stop_command_id = stop.command_id
    process_started = asyncio.Event()

    async def fake_create_subprocess(*args: object, **kwargs: object) -> _FakeProcess:
        del args, kwargs
        process_started.set()
        return _FakeProcess([b'{"event":"qr","qr_code_url":"data:image/png;base64,AA=="}\n'])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)
    task = asyncio.create_task(manager._run(session))
    await asyncio.sleep(0)
    assert not process_started.is_set()

    claimed = commands.claim_next()
    assert claimed is not None
    commands.complete(claimed.command_id, result={"state": "qr_pending"})
    await task

    assert process_started.is_set()
    assert session.qr_code_url == "data:image/png;base64,AA=="


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "message"),
    [
        ("failed", "账号连接进程停止失败，请重新尝试"),
        ("timeout", "账号连接进程停止超时，请重新尝试"),
    ],
)
async def test_run_marks_stop_failure_as_retryable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    message: str,
) -> None:
    manager, commands, _ = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7)
    stop = commands.enqueue("account-1", AccountCommand.RELOGIN_QR, "qr-stop-1", {})
    session.stop_command_id = stop.command_id
    started = False

    async def fake_create_subprocess(*args: object, **kwargs: object) -> _FakeProcess:
        del args, kwargs
        nonlocal started
        started = True
        return _FakeProcess([])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)
    if status == "failed":
        claimed = commands.claim_next()
        assert claimed is not None
        commands.complete(claimed.command_id, error_message="stop failed")
    else:
        monkeypatch.setattr(
            "xianyu_connector.application.qr_auth_manager.ACCOUNT_STOP_TIMEOUT_SECONDS",
            0.01,
        )

    await manager._run(session)

    assert started is False
    assert session.state == "error"
    assert session.message == message


@pytest.mark.asyncio
async def test_run_marks_unstructured_process_exit_as_error(tmp_path: Path, monkeypatch) -> None:
    manager, _, _ = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7)
    process = _FakeProcess([], return_code=30)

    async def fake_create_subprocess(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)

    await manager._run(session)

    assert session.state == "error"
    assert session.message == "二维码生成失败，请重新尝试"


@pytest.mark.asyncio
async def test_run_marks_worker_start_failure_as_retryable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _, _ = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7)

    async def fail_to_start(*args: object, **kwargs: object) -> _FakeProcess:
        del args, kwargs
        raise FileNotFoundError("python executable unavailable")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_to_start)

    await manager._run(session)

    assert session.state == "error"
    assert session.message == "二维码生成失败，请重新尝试"
    assert manager._processes == {}


@pytest.mark.asyncio
async def test_run_timeout_and_process_helpers_fail_closed(tmp_path: Path, monkeypatch) -> None:
    manager, _, _ = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7)
    process = _FakeProcess([])
    process.returncode = 0

    async def fake_create_subprocess(*args, **kwargs):
        return process

    async def timeout(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)
    monkeypatch.setattr(manager, "_consume_events", timeout)

    await manager._run(session)
    await drain_stream(None)
    await terminate_process(process)

    assert session.state == "expired"
    assert _public_status("new") == "waiting"
    assert _public_status("confirmed") == "confirmed"
    assert _public_status("authenticated") == "processing"


@pytest.mark.asyncio
async def test_event_consumer_skips_invalid_json_and_applies_terminal_error(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7)
    process = _FakeProcess([b"not-json\n", b'{"event":"error"}\n'])

    await manager._consume_events(session, process)

    assert session.state == "error"
    assert session.message == "认证失败"


@pytest.mark.asyncio
async def test_event_consumer_rejects_process_without_stdout(tmp_path: Path) -> None:
    manager, _, _ = _manager(tmp_path)
    session = QrAuthSession("session-1", "account-1", 7)

    class NoStdoutProcess:
        stdout = None

    with pytest.raises(RuntimeError, match="stdout is unavailable"):
        await manager._consume_events(session, NoStdoutProcess())


@pytest.mark.asyncio
async def test_wait_until_online_returns_when_authenticated_runtime_requires_verification(
    tmp_path: Path,
) -> None:
    manager, _, runtimes = _manager(tmp_path)
    session = QrAuthSession(
        "session-1",
        "account-1",
        7,
        state="processing",
        authenticated=True,
        authenticated_at=None,
    )
    RuntimeReporter("account-1", runtimes).require_manual_verification("risk", "verify now")

    await manager._wait_until_online(session)

    assert session.state == "verification_required"
    assert session.message == "verify now"


@pytest.mark.asyncio
async def test_close_terminates_process_cancels_task_and_releases_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = AccountOperationCoordinator()
    manager, _, _ = _manager(tmp_path, coordinator=coordinator)
    session = QrAuthSession("session-1", "account-1", 7)
    session.operation_lease = coordinator.reserve("account-1", AccountOperationKind.QR)
    manager._sessions[session.session_id] = session
    process = _FakeProcess([])
    manager._processes[session.session_id] = process
    blocker = asyncio.Event()
    task = asyncio.create_task(blocker.wait())
    manager._tasks[session.session_id] = task
    terminated: list[object] = []

    async def record_termination(candidate: object) -> None:
        terminated.append(candidate)

    monkeypatch.setattr(
        "xianyu_connector.application.qr_auth_manager.terminate_process",
        record_termination,
    )

    await manager.close()

    assert terminated == [process]
    assert task.cancelled()
    assert coordinator.active_kind("account-1") is None


class _FakeStream:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


class _FakeProcess:
    def __init__(self, lines: list[bytes], return_code: int = 0) -> None:
        self.stdout = _FakeStream(lines)
        self.stderr = _FakeStream([])
        self.pid = 4242
        self.returncode: int | None = None
        self._return_code = return_code

    async def wait(self) -> int:
        self.returncode = self._return_code
        return self._return_code
