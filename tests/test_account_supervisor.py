from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from xianyu_connector.application.account_supervisor import (
    AccountSupervisor,
    _failure_from_exit_code,
)
from xianyu_connector.application.runtime_service import RuntimeService
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.domain.commands import AccountCommand, CommandStatus
from xianyu_connector.domain.recovery_policy import FailureKind
from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_connector.infrastructure.sqlite_command_repository import SqliteCommandRepository
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository
from xianyu_connector.infrastructure.worker_process import WorkerExitCode


@dataclass
class FakeWorker:
    pid: int
    alive: bool = True
    exit_code: int | None = None

    def is_alive(self) -> bool:
        return self.alive

    def return_code(self) -> int | None:
        return self.exit_code

    def stop(self, grace_seconds: float = 10) -> None:
        self.alive = False
        self.exit_code = 0


class FakeWorkerFactory:
    def __init__(self) -> None:
        self.started: list[FakeWorker] = []

    def start(self, account_id: str) -> FakeWorker:
        worker = FakeWorker(pid=1000 + len(self.started))
        self.started.append(worker)
        return worker


class FailingWorkerFactory:
    def start(self, account_id: str) -> FakeWorker:
        del account_id
        raise OSError("worker process unavailable")


def _dependencies(tmp_path: Path):
    database_path = tmp_path / "supervisor.db"
    with sqlite3.connect(database_path) as connection:
        apply_connector_schema(connection)
    runtime_repository = SqliteRuntimeRepository(database_path)
    command_repository = SqliteCommandRepository(database_path)
    factory = FakeWorkerFactory()
    supervisor = AccountSupervisor(runtime_repository, command_repository, factory)
    return runtime_repository, command_repository, factory, supervisor


def test_start_command_is_idempotent_and_persists_worker_pid(tmp_path: Path) -> None:
    runtime_repository, commands, factory, supervisor = _dependencies(tmp_path)
    first = commands.enqueue("account-1", AccountCommand.START, "start-1", {})
    second = commands.enqueue("account-1", AccountCommand.START, "start-1", {})

    completed = supervisor.process_next_command()

    assert first.command_id == second.command_id == completed.command_id
    assert completed.status is CommandStatus.SUCCEEDED
    assert len(factory.started) == 1
    runtime = runtime_repository.get("account-1")
    assert runtime.state is AccountState.AUTHENTICATING
    assert runtime.worker_pid == 1000


def test_worker_start_failure_converges_runtime_without_authenticating_ghost(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "start-failure.db"
    with sqlite3.connect(database_path) as connection:
        apply_connector_schema(connection)
    runtime_repository = SqliteRuntimeRepository(database_path)
    commands = SqliteCommandRepository(database_path)
    supervisor = AccountSupervisor(runtime_repository, commands, FailingWorkerFactory())

    commands.enqueue("account-1", AccountCommand.START, "start-failure", {})
    completed = supervisor.process_next_command()

    assert completed is not None
    assert completed.status is CommandStatus.FAILED
    runtime = runtime_repository.get("account-1")
    assert runtime.state is AccountState.FAILED
    assert runtime.reason_code == "worker_start_failed"
    assert runtime.worker_pid is None


def test_automatic_recovery_start_failure_does_not_escape_supervise(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "recovery-start-failure.db"
    with sqlite3.connect(database_path) as connection:
        apply_connector_schema(connection)
    runtime_repository = SqliteRuntimeRepository(database_path)
    commands = SqliteCommandRepository(database_path)
    RuntimeService(runtime_repository).transition_to("account-1", AccountState.RECOVERING)
    supervisor = AccountSupervisor(runtime_repository, commands, FailingWorkerFactory())

    supervisor.supervise(datetime(2026, 7, 17, tzinfo=UTC))

    runtime = runtime_repository.get("account-1")
    assert runtime.state is AccountState.FAILED
    assert runtime.reason_code == "worker_start_failed"


def test_stop_failure_keeps_worker_owned_by_supervisor(tmp_path: Path) -> None:
    _runtime_repository, commands, factory, supervisor = _dependencies(tmp_path)
    commands.enqueue("account-1", AccountCommand.START, "start-before-stop-failure", {})
    supervisor.process_next_command()

    worker = factory.started[0]

    def failed_stop(grace_seconds: float = 10) -> None:
        del grace_seconds
        raise OSError("worker stop failed")

    worker.stop = failed_stop  # type: ignore[method-assign]
    commands.enqueue("account-1", AccountCommand.STOP, "stop-failure", {})
    completed = supervisor.process_next_command()

    assert completed is not None
    assert completed.status is CommandStatus.FAILED
    assert supervisor._workers["account-1"] is worker


def test_session_expired_worker_requires_manual_verification(tmp_path: Path) -> None:
    runtime_repository, commands, factory, supervisor = _dependencies(tmp_path)
    commands.enqueue("account-1", AccountCommand.START, "start-1", {})
    supervisor.process_next_command()
    factory.started[0].alive = False
    factory.started[0].exit_code = 21

    supervisor.supervise(datetime(2026, 7, 17, tzinfo=UTC))

    runtime = runtime_repository.get("account-1")
    assert runtime.state is AccountState.MANUAL_VERIFICATION_REQUIRED
    assert runtime.next_action_at is None


def test_crashed_worker_is_restarted_after_persisted_backoff(tmp_path: Path) -> None:
    runtime_repository, commands, factory, supervisor = _dependencies(tmp_path)
    commands.enqueue("account-1", AccountCommand.START, "start-1", {})
    supervisor.process_next_command()
    factory.started[0].alive = False
    factory.started[0].exit_code = 1
    now = datetime(2026, 7, 17, tzinfo=UTC)

    supervisor.supervise(now)
    assert runtime_repository.get("account-1").state is AccountState.RECOVERING
    assert len(factory.started) == 1

    supervisor.supervise(now + timedelta(seconds=11))
    assert len(factory.started) == 2
    assert runtime_repository.get("account-1").state is AccountState.AUTHENTICATING


def test_token_expiry_restarts_once_then_requires_manual_verification(tmp_path: Path) -> None:
    runtime_repository, commands, factory, supervisor = _dependencies(tmp_path)
    commands.enqueue("account-1", AccountCommand.START, "start-token", {})
    supervisor.process_next_command()
    now = datetime(2026, 7, 17, tzinfo=UTC)

    factory.started[0].alive = False
    factory.started[0].exit_code = WorkerExitCode.TOKEN_EXPIRED
    supervisor.supervise(now)

    assert len(factory.started) == 2
    assert runtime_repository.get("account-1").state is AccountState.AUTHENTICATING

    factory.started[1].alive = False
    factory.started[1].exit_code = WorkerExitCode.TOKEN_EXPIRED
    supervisor.supervise(now + timedelta(seconds=1))

    runtime = runtime_repository.get("account-1")
    assert len(factory.started) == 2
    assert runtime.state is AccountState.MANUAL_VERIFICATION_REQUIRED
    assert runtime.reason_code == "token_expired"


def test_stale_worker_heartbeat_forces_bounded_restart(tmp_path: Path) -> None:
    runtime_repository, commands, factory, supervisor = _dependencies(tmp_path)
    commands.enqueue("account-1", AccountCommand.START, "start-stale", {})
    supervisor.process_next_command()
    runtime = runtime_repository.get("account-1")
    observed = replace(
        runtime,
        worker_heartbeat_at=datetime(2026, 7, 17, tzinfo=UTC),
    )
    runtime_repository.save_observation(runtime, observed)

    supervisor.supervise(datetime(2026, 7, 17, 0, 0, 31, tzinfo=UTC))

    assert factory.started[0].alive is False
    assert runtime_repository.get("account-1").state is AccountState.RECOVERING


def test_relogin_stops_worker_and_clears_runtime_pid(tmp_path: Path) -> None:
    runtime_repository, commands, factory, supervisor = _dependencies(tmp_path)
    commands.enqueue("account-1", AccountCommand.START, "start-before-qr", {})
    supervisor.process_next_command()
    commands.enqueue("account-1", AccountCommand.RELOGIN_QR, "relogin-1", {})

    completed = supervisor.process_next_command()

    runtime = runtime_repository.get("account-1")
    assert completed.status is CommandStatus.SUCCEEDED
    assert runtime.state is AccountState.QR_PENDING
    assert runtime.worker_pid is None
    assert factory.started[0].alive is False


def test_relogin_qr_resets_recovery_backoff_state(tmp_path: Path) -> None:
    runtime_repository, commands, factory, supervisor = _dependencies(tmp_path)
    current = runtime_repository.ensure("account-1")
    observed = replace(
        current,
        restart_count=26,
        next_action_at=datetime(2026, 7, 17, 0, 5, tzinfo=UTC),
    )
    runtime_repository.save_observation(current, observed)

    commands.enqueue("account-1", AccountCommand.RELOGIN_QR, "relogin-reset-1", {})
    completed = supervisor.process_next_command()

    assert completed.status is CommandStatus.SUCCEEDED
    runtime = runtime_repository.get("account-1")
    assert runtime.state is AccountState.QR_PENDING
    assert runtime.restart_count == 0
    assert runtime.next_action_at is None

    commands.enqueue("account-1", AccountCommand.START, "start-after-qr", {})
    supervisor.process_next_command()
    factory.started[0].alive = False
    factory.started[0].exit_code = WorkerExitCode.NETWORK_FAILURE
    failed_at = datetime(2026, 7, 17, 0, 10, tzinfo=UTC)

    supervisor.supervise(failed_at)

    recovering = runtime_repository.get("account-1")
    assert recovering.state is AccountState.RECOVERING
    assert recovering.restart_count == 1
    assert recovering.next_action_at == failed_at + timedelta(seconds=5)

    supervisor.supervise(failed_at + timedelta(seconds=5))
    assert len(factory.started) == 2


def test_control_commands_cover_duplicate_stop_resume_and_reconnect(tmp_path: Path) -> None:
    runtime_repository, commands, factory, supervisor = _dependencies(tmp_path)
    commands.enqueue("account-1", AccountCommand.START, "start-control", {})
    supervisor.process_next_command()
    commands.enqueue("account-1", AccountCommand.START, "start-duplicate", {})
    duplicate = supervisor.process_next_command()
    assert duplicate.result["state"] == "already_running"

    commands.enqueue("account-1", AccountCommand.STOP, "stop-control", {})
    supervisor.process_next_command()
    assert runtime_repository.get("account-1").state is AccountState.OFFLINE

    commands.enqueue(
        "account-1",
        AccountCommand.RESUME_AFTER_VERIFICATION,
        "resume-control",
        {},
    )
    supervisor.process_next_command()
    commands.enqueue("account-1", AccountCommand.RECONNECT, "reconnect-control", {})
    supervisor.process_next_command()

    assert len(factory.started) == 3
    supervisor.stop_all()
    assert factory.started[-1].alive is False


@pytest.mark.parametrize(
    ("exit_code", "failure"),
    [
        (WorkerExitCode.MANUAL_VERIFICATION, FailureKind.RISK_CHALLENGE),
        (WorkerExitCode.RISK_CHALLENGE, FailureKind.RISK_CHALLENGE),
        (WorkerExitCode.NETWORK_FAILURE, FailureKind.NETWORK),
        (WorkerExitCode.TOKEN_EXPIRED, FailureKind.TOKEN_EXPIRED),
        (WorkerExitCode.STOPPED, FailureKind.UNKNOWN),
        (None, FailureKind.UNKNOWN),
    ],
)
def test_worker_exit_code_classification(exit_code: int | None, failure: FailureKind) -> None:
    assert _failure_from_exit_code(exit_code) is failure
