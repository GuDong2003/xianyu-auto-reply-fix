from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from xianyu_connector.application.account_operation_coordinator import (
    AccountOperationConflict,
    AccountOperationCoordinator,
    AccountOperationKind,
    AccountOperationLease,
)
from xianyu_connector.application.account_supervisor import AccountSupervisor
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.domain.commands import AccountCommand, CommandStatus
from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_connector.infrastructure.sqlite_command_repository import SqliteCommandRepository
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository
from xianyu_connector.infrastructure.worker_process import WorkerExitCode


class _FakeWorker:
    pid = 1001

    def __init__(self) -> None:
        self.alive = True
        self.exit_code: int | None = None

    def is_alive(self) -> bool:
        return self.alive

    def return_code(self) -> int | None:
        return self.exit_code

    def stop(self, grace_seconds: float = 10) -> None:
        del grace_seconds
        self.alive = False
        self.exit_code = WorkerExitCode.STOPPED


class _FakeFactory:
    def __init__(self) -> None:
        self.started: list[_FakeWorker] = []

    def start(self, account_id: str) -> _FakeWorker:
        del account_id
        worker = _FakeWorker()
        self.started.append(worker)
        return worker


def _supervisor(tmp_path: Path) -> tuple[AccountOperationCoordinator, AccountSupervisor, SqliteCommandRepository, SqliteRuntimeRepository, _FakeFactory]:
    database_path = tmp_path / "operations.db"
    with sqlite3.connect(database_path) as connection:
        apply_connector_schema(connection)
    commands = SqliteCommandRepository(database_path)
    runtimes = SqliteRuntimeRepository(database_path)
    factory = _FakeFactory()
    coordinator = AccountOperationCoordinator()
    supervisor = AccountSupervisor(runtimes, commands, factory, coordinator=coordinator)
    return coordinator, supervisor, commands, runtimes, factory


@pytest.mark.asyncio
async def test_same_account_reservations_are_exclusive_but_different_accounts_are_independent() -> None:
    coordinator = AccountOperationCoordinator()
    qr = coordinator.reserve("account-1", AccountOperationKind.QR)

    with pytest.raises(AccountOperationConflict):
        coordinator.reserve("account-1", AccountOperationKind.MANUAL_VERIFICATION)

    other = await coordinator.reserve_async("account-2", AccountOperationKind.MANUAL_VERIFICATION)
    assert coordinator.active_kind("account-1") is AccountOperationKind.QR
    assert coordinator.active_kind("account-2") is AccountOperationKind.MANUAL_VERIFICATION

    coordinator.release(other)
    coordinator.release(qr)
    assert coordinator.active_kind("account-1") is None
    assert coordinator.active_kind("account-2") is None


@pytest.mark.asyncio
async def test_sync_and_async_holds_fail_fast_while_account_mutex_is_held() -> None:
    coordinator = AccountOperationCoordinator()

    with coordinator.hold("account-1"):
        with (
            pytest.raises(AccountOperationConflict, match="account operation is busy"),
            coordinator.hold("account-1", timeout_seconds=0),
        ):
            pass
        with pytest.raises(AccountOperationConflict, match="account operation is busy"):
            async with coordinator.hold_async("account-1", timeout_seconds=0):
                pass

    async with coordinator.hold_async("account-1", timeout_seconds=0.1):
        assert coordinator.active_kind("account-1") is None


@pytest.mark.asyncio
async def test_async_hold_waits_through_contention_then_acquires_after_release() -> None:
    coordinator = AccountOperationCoordinator()
    entered = asyncio.Event()

    async def wait_for_account() -> None:
        async with coordinator.hold_async("account-1", timeout_seconds=0.5):
            entered.set()

    with coordinator.hold("account-1"):
        waiter = asyncio.create_task(wait_for_account())
        await asyncio.sleep(0.12)
        assert not entered.is_set()

    await waiter
    assert entered.is_set()


@pytest.mark.asyncio
async def test_release_rejects_forged_lease_but_duplicate_release_is_idempotent() -> None:
    coordinator = AccountOperationCoordinator()
    lease = coordinator.reserve("account-1", AccountOperationKind.QR)
    forged = AccountOperationLease(
        account_id="account-1",
        kind=AccountOperationKind.QR,
        token="forged-token",
    )

    with pytest.raises(AccountOperationConflict, match="lease mismatch"):
        coordinator.release(forged)

    assert coordinator.active_kind("account-1") is AccountOperationKind.QR
    await coordinator.release_async(lease)
    coordinator.release(lease)
    assert coordinator.active_kind("account-1") is None


def test_supervisor_rejects_normal_start_while_manual_operation_is_reserved(tmp_path: Path) -> None:
    coordinator, supervisor, commands, runtimes, factory = _supervisor(tmp_path)
    coordinator.reserve("account-1", AccountOperationKind.MANUAL_VERIFICATION)

    command = commands.enqueue("account-1", AccountCommand.START, "blocked-start", {})
    completed = supervisor.process_next_command()

    assert completed is not None
    assert completed.command_id == command.command_id
    assert completed.status is CommandStatus.FAILED
    assert factory.started == []
    runtime = runtimes.get("account-1")
    assert runtime is None or runtime.state is not AccountState.AUTHENTICATING


def test_supervisor_allows_qr_relogin_to_replace_qr_reservation(tmp_path: Path) -> None:
    coordinator, supervisor, commands, runtimes, _factory = _supervisor(tmp_path)
    lease = coordinator.reserve("account-1", AccountOperationKind.QR)

    commands.enqueue("account-1", AccountCommand.RELOGIN_QR, "qr-relogin", {})
    completed = supervisor.process_next_command()

    assert completed is not None
    assert completed.status is CommandStatus.SUCCEEDED
    assert runtimes.get("account-1").state is AccountState.QR_PENDING
    coordinator.release(lease)
