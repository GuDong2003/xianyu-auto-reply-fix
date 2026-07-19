from __future__ import annotations

import asyncio
import base64
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from xianyu_connector.application.account_operation_coordinator import (
    AccountOperationConflict,
    AccountOperationCoordinator,
    AccountOperationKind,
)
from xianyu_connector.application.runtime_service import RuntimeService
from xianyu_connector.application.verification_runtime import (
    VerificationCoordinator,
    VerificationRfbForbidden,
    VerificationUnavailable,
)
from xianyu_connector.application.verification_session_manager import (
    InvalidVerificationToken,
    VerificationSessionNotFound,
)
from xianyu_connector.domain.account_state import (
    AccountReadiness,
    AccountRuntime,
    AccountState,
)
from xianyu_connector.domain.commands import AccountCommand, CommandRecord
from xianyu_connector.domain.verification_session import VerificationSessionState
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
from xianyu_connector.security.aes_gcm import SecretCipher

CHALLENGE_URL = "https://challenge.goofish.com/verify?secret=hidden"


class FakeRfbLease:
    def __init__(self, touch_callback: Any) -> None:
        self._touch_callback = touch_callback
        self.closed = False

    def touch(self) -> None:
        self._touch_callback()

    def close(self) -> None:
        self.closed = True


class FakeVerificationProcess:
    def __init__(
        self,
        *,
        state: dict[str, object] | None = None,
        error: dict[str, object] | None = None,
        completion_error: RuntimeError | None = None,
    ) -> None:
        self.state = state
        self.error = error
        self.completion_error = completion_error
        self.complete_calls = 0
        self.last_lease: FakeRfbLease | None = None

    def get_state(self) -> dict[str, object] | None:
        return self.state

    def get_error(self) -> dict[str, object] | None:
        return self.error

    def complete(self) -> None:
        self.complete_calls += 1
        if self.completion_error:
            raise self.completion_error

    def acquire_rfb(self, *, touch_callback: Any) -> FakeRfbLease:
        self.last_lease = FakeRfbLease(touch_callback)
        return self.last_lease


class FakeProcessSupervisor:
    def __init__(self) -> None:
        self.start_error: Exception | None = None
        self.next_process: FakeVerificationProcess | None = None
        self.processes: dict[tuple[str, str], FakeVerificationProcess] = {}
        self.start_calls: list[tuple[str, str, str]] = []
        self.stop_calls: list[tuple[str, str | None]] = []
        self.stop_all_calls = 0

    def start(
        self,
        account_id: str,
        session_id: str,
        challenge_url: str,
    ) -> FakeVerificationProcess:
        self.start_calls.append((account_id, session_id, challenge_url))
        if self.start_error:
            raise self.start_error
        process = self.next_process or FakeVerificationProcess(
            state={"state": "rfb_ready"},
        )
        self.next_process = None
        self.processes[(account_id, session_id)] = process
        return process

    def get(
        self,
        account_id: str,
        session_id: str | None = None,
    ) -> FakeVerificationProcess | None:
        if session_id is not None:
            return self.processes.get((account_id, session_id))
        return next(
            (process for (owner, _), process in self.processes.items() if owner == account_id),
            None,
        )

    def stop(self, account_id: str, session_id: str | None = None) -> None:
        self.stop_calls.append((account_id, session_id))
        keys = [
            key
            for key in self.processes
            if key[0] == account_id and (session_id is None or key[1] == session_id)
        ]
        for key in keys:
            self.processes.pop(key, None)

    def stop_all(self) -> None:
        self.stop_all_calls += 1
        self.processes.clear()


@dataclass
class VerificationHarness:
    coordinator: VerificationCoordinator
    runtimes: SqliteRuntimeRepository
    commands: SqliteCommandRepository
    secrets: SqliteSecretRepository
    operations: AccountOperationCoordinator
    processes: FakeProcessSupervisor


def _build_harness(
    tmp_path: Path,
    *,
    prepare_runtime: bool = True,
    prepare_challenge: bool = True,
) -> VerificationHarness:
    database_path = tmp_path / "verification-runtime.db"
    with sqlite3.connect(database_path) as connection:
        apply_connector_schema(connection)
    key_path = tmp_path / "master.key"
    key_path.write_bytes(base64.urlsafe_b64encode(b"k" * 32))
    runtimes = SqliteRuntimeRepository(database_path)
    if prepare_runtime:
        RuntimeService(runtimes).transition_to(
            "account-1",
            AccountState.MANUAL_VERIFICATION_REQUIRED,
        )
    commands = SqliteCommandRepository(database_path)
    operations = AccountOperationCoordinator()
    coordinator = VerificationCoordinator(
        database_path,
        tmp_path / "profiles",
        key_path,
        runtimes,
        commands,
        coordinator=operations,
    )
    processes = FakeProcessSupervisor()
    coordinator._processes = processes  # type: ignore[assignment]
    secrets = SqliteSecretRepository(database_path, SecretCipher(b"k" * 32))
    if prepare_challenge:
        secrets.save("account-1", "verification_url", CHALLENGE_URL)
    return VerificationHarness(
        coordinator,
        runtimes,
        commands,
        secrets,
        operations,
        processes,
    )


@pytest.mark.parametrize(
    ("prepare_runtime", "prepare_challenge", "message"),
    [
        (False, True, "账号当前没有待处理的人工验证"),
        (True, False, "没有可用的验证会话"),
    ],
)
def test_create_rejects_when_no_actionable_challenge_exists(
    tmp_path: Path,
    prepare_runtime: bool,
    prepare_challenge: bool,
    message: str,
) -> None:
    harness = _build_harness(
        tmp_path,
        prepare_runtime=prepare_runtime,
        prepare_challenge=prepare_challenge,
    )

    with pytest.raises(VerificationUnavailable, match=message):
        harness.coordinator.create("account-1", "operator-1", "request-1")

    assert harness.processes.start_calls == []


def test_create_translates_reservation_race_to_safe_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _build_harness(tmp_path)

    def conflict(*args: object) -> None:
        del args
        raise AccountOperationConflict("concurrent reservation")

    monkeypatch.setattr(harness.operations, "reserve", conflict)

    with pytest.raises(VerificationUnavailable, match="其他操作"):
        harness.coordinator.create("account-1", "operator-1", "request-1")


def test_conflicting_session_releases_temporary_operation_lease(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)
    harness.coordinator._sessions.create("account-1", "operator-1", "existing")

    with pytest.raises(VerificationUnavailable, match="已有其他人工验证会话"):
        harness.coordinator.create("account-1", "operator-2", "conflicting")

    assert harness.operations.active_kind("account-1") is None
    qr_lease = harness.operations.reserve("account-1", AccountOperationKind.QR)
    harness.operations.release(qr_lease)


def test_idempotent_replay_does_not_restart_browser_or_hold_temporary_lease(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path)
    seeded = harness.coordinator._sessions.create(
        "account-1",
        "operator-1",
        "same-request",
    )

    replay = harness.coordinator.create(
        "account-1",
        "operator-1",
        "same-request",
    )

    assert replay["session_id"] == seeded.session.session_id
    assert replay["access_token"] != seeded.access_token
    assert harness.processes.start_calls == []
    assert harness.operations.active_kind("account-1") is None


def test_browser_start_failure_is_persisted_and_can_be_retried(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)
    harness.processes.start_error = RuntimeError("browser failed")

    with pytest.raises(VerificationUnavailable, match="验证浏览器启动失败"):
        harness.coordinator.create("account-1", "operator-1", "browser-start")

    failed = harness.coordinator._sessions.active_for_account("account-1")
    assert failed is None
    assert harness.operations.active_kind("account-1") is None

    harness.processes.start_error = None
    retried = harness.coordinator.create(
        "account-1",
        "operator-1",
        "browser-start",
    )
    assert retried["state"] == VerificationSessionState.WAITING_FOR_OPERATOR.value


def test_get_frame_and_input_enforce_authorization_and_expose_browser_state(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path)
    harness.processes.next_process = FakeVerificationProcess(
        state={"state": "rfb_ready"},
        error={"code": "challenge_refresh_failed"},
    )
    created = harness.coordinator.create("account-1", "operator-1", "access")
    session_id = str(created["session_id"])

    with pytest.raises(InvalidVerificationToken, match="ticket is required"):
        harness.coordinator.get("account-1", session_id, None, 0)
    with pytest.raises(VerificationSessionNotFound):
        harness.coordinator.get(
            "account-2",
            session_id,
            str(created["access_token"]),
            0,
        )

    activated = harness.coordinator.get(
        "account-1",
        session_id,
        str(created["access_token"]),
        0,
    )
    frame = harness.coordinator.frame("account-1", session_id, None, 42)

    assert activated["state"] == VerificationSessionState.OPERATOR_ACTIVE.value
    assert frame["browser_state"] == "rfb_ready"
    assert frame["failure_code"] == "challenge_refresh_failed"
    with pytest.raises(VerificationUnavailable, match="RFB"):
        harness.coordinator.input(
            "account-1",
            session_id,
            None,
            {"type": "pointermove"},
        )


def test_requested_session_is_not_operable(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)
    seeded = harness.coordinator._sessions.create(
        "account-1",
        "operator-1",
        "requested-only",
    )

    with pytest.raises(VerificationUnavailable, match="当前不可操作"):
        harness.coordinator.get(
            "account-1",
            seeded.session.session_id,
            seeded.access_token,
            0,
        )


def test_rfb_requires_active_session_and_live_browser(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)
    created = harness.coordinator.create("account-1", "operator-1", "rfb")
    session_id = str(created["session_id"])

    with pytest.raises(VerificationRfbForbidden):
        harness.coordinator.open_rfb("account-1", session_id, "operator-2")
    with pytest.raises(VerificationSessionNotFound):
        harness.coordinator.open_rfb("account-2", session_id, "operator-1")
    with pytest.raises(VerificationUnavailable, match="不接受 RFB"):
        harness.coordinator.open_rfb("account-1", session_id, "operator-1")

    harness.coordinator.get(
        "account-1",
        session_id,
        str(created["access_token"]),
        0,
    )
    harness.processes.stop("account-1", session_id)
    with pytest.raises(VerificationUnavailable, match="浏览器已退出"):
        harness.coordinator.open_rfb("account-1", session_id, "operator-1")


def test_rfb_activity_touches_the_authorized_session(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)
    created = harness.coordinator.create("account-1", "operator-1", "rfb-touch")
    session_id = str(created["session_id"])
    harness.coordinator.get(
        "account-1",
        session_id,
        str(created["access_token"]),
        0,
    )
    before = harness.coordinator._sessions.get(session_id).last_activity_at

    lease = harness.coordinator.open_rfb("account-1", session_id, "operator-1")
    lease.touch()

    assert harness.coordinator._sessions.get(session_id).last_activity_at >= before


def test_completion_failure_is_terminal_and_cleans_up_on_reinspection(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path)
    harness.processes.next_process = FakeVerificationProcess(
        completion_error=RuntimeError("completion rejected"),
    )
    created = harness.coordinator.create("account-1", "operator-1", "complete-error")
    session_id = str(created["session_id"])
    harness.coordinator.get(
        "account-1",
        session_id,
        str(created["access_token"]),
        0,
    )

    with pytest.raises(VerificationUnavailable, match="未能保存验证结果"):
        harness.coordinator.complete("account-1", session_id, None)

    failed = harness.coordinator.get("account-1", session_id, None, 0)
    assert failed["state"] == VerificationSessionState.FAILED.value
    assert failed["reason_code"] == "browser_completion_failed"
    assert harness.operations.active_kind("account-1") is None


def test_completion_requires_a_live_browser(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)
    created = harness.coordinator.create("account-1", "operator-1", "missing-browser")
    session_id = str(created["session_id"])
    harness.coordinator.get(
        "account-1",
        session_id,
        str(created["access_token"]),
        0,
    )
    harness.processes.stop("account-1", session_id)

    with pytest.raises(VerificationUnavailable, match="浏览器已退出"):
        harness.coordinator.complete("account-1", session_id, None)


@pytest.mark.asyncio
async def test_completion_submits_stops_releases_and_starts_recovery(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)
    process = FakeVerificationProcess()
    harness.processes.next_process = process
    created = harness.coordinator.create("account-1", "operator-1", "complete")
    session_id = str(created["session_id"])
    harness.coordinator.get(
        "account-1",
        session_id,
        str(created["access_token"]),
        0,
    )

    completed = harness.coordinator.complete("account-1", session_id, None)
    recovery_task = harness.coordinator._recovery_tasks[session_id]

    assert completed["state"] == VerificationSessionState.VERIFYING.value
    assert process.complete_calls == 1
    assert harness.processes.get("account-1", session_id) is None
    assert harness.operations.active_kind("account-1") is None
    await recovery_task
    failed = harness.coordinator._sessions.get(session_id)
    assert failed.state is VerificationSessionState.FAILED
    assert failed.reason_code == "recovery_failed"


def test_cancel_and_terminal_calls_are_idempotent(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)
    created = harness.coordinator.create("account-1", "operator-1", "cancel")
    session_id = str(created["session_id"])

    cancelled = harness.coordinator.cancel(
        "account-1",
        session_id,
        str(created["access_token"]),
    )
    repeated = harness.coordinator.cancel("account-1", session_id, None)
    completed = harness.coordinator.complete("account-1", session_id, None)

    assert cancelled["state"] == VerificationSessionState.CANCELLED.value
    assert repeated["state"] == VerificationSessionState.CANCELLED.value
    assert completed["state"] == VerificationSessionState.CANCELLED.value
    assert harness.operations.active_kind("account-1") is None


def _prepare_verifying_session(harness: VerificationHarness, key: str) -> str:
    created = harness.coordinator.create("account-1", "operator-1", key)
    session_id = str(created["session_id"])
    harness.coordinator.get(
        "account-1",
        session_id,
        str(created["access_token"]),
        0,
    )
    harness.coordinator._sessions.submit(session_id)
    harness.coordinator._sessions.begin_verification(session_id)
    return session_id


class StaticRuntimeRepository:
    def __init__(self, runtime: AccountRuntime | None) -> None:
        self.runtime = runtime

    def get(self, account_id: str) -> AccountRuntime | None:
        del account_id
        return self.runtime


@pytest.mark.asyncio
async def test_recovery_success_clears_challenge_and_cleans_resources(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path)
    session_id = _prepare_verifying_session(harness, "recover-success")
    online = AccountRuntime(
        "account-1",
        state=AccountState.ONLINE,
        readiness=AccountReadiness(True, True, True, True),
    )
    harness.coordinator._runtimes = StaticRuntimeRepository(online)  # type: ignore[assignment]

    await harness.coordinator._recover_account("account-1", session_id)

    assert harness.coordinator._sessions.get(session_id).state is VerificationSessionState.SUCCEEDED
    assert harness.secrets.get("account-1", "verification_url") == ""
    queued = harness.commands.claim_next()
    assert queued is not None
    assert queued.command is AccountCommand.RESUME_AFTER_VERIFICATION
    assert session_id not in harness.coordinator._recovery_commands
    assert harness.processes.get("account-1", session_id) is None


@pytest.mark.asyncio
async def test_recovery_timeout_marks_session_failed_without_sleeping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _build_harness(tmp_path)
    session_id = _prepare_verifying_session(harness, "recover-timeout")
    harness.coordinator._runtimes = StaticRuntimeRepository(None)  # type: ignore[assignment]

    class AdvancingLoop:
        times = iter((0.0, 91.0))

        def time(self) -> float:
            return next(self.times)

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: AdvancingLoop())

    await harness.coordinator._recover_account("account-1", session_id)

    failed = harness.coordinator._sessions.get(session_id)
    assert failed.state is VerificationSessionState.FAILED
    assert failed.reason_code == "recovery_failed"


@pytest.mark.asyncio
async def test_recovery_exception_is_compensated_and_maps_are_cleaned(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path)
    session_id = _prepare_verifying_session(harness, "recover-error")

    class FailingCommands:
        def enqueue(self, *args: object, **kwargs: object) -> CommandRecord:
            del args, kwargs
            raise RuntimeError("command store unavailable")

    harness.coordinator._commands = FailingCommands()  # type: ignore[assignment]
    harness.coordinator._recovery_commands[session_id] = "stale-command"

    await harness.coordinator._recover_account("account-1", session_id)

    failed = harness.coordinator._sessions.get(session_id)
    assert failed.state is VerificationSessionState.FAILED
    assert failed.reason_code == "recovery_error"
    assert session_id not in harness.coordinator._recovery_commands
    assert session_id not in harness.coordinator._recovery_tasks


@pytest.mark.asyncio
async def test_close_cancels_recovery_and_releases_all_operations(tmp_path: Path) -> None:
    harness = _build_harness(tmp_path)
    created = harness.coordinator.create("account-1", "operator-1", "close")
    session_id = str(created["session_id"])
    pending = asyncio.create_task(asyncio.sleep(60))
    harness.coordinator._recovery_tasks[session_id] = pending
    harness.coordinator._recovery_commands[session_id] = "command-1"

    harness.coordinator._start_recovery("account-1", session_id)
    harness.coordinator.close()

    with pytest.raises(asyncio.CancelledError):
        await pending
    assert harness.processes.stop_all_calls == 1
    assert harness.coordinator._recovery_tasks == {}
    assert harness.coordinator._recovery_commands == {}
    assert harness.operations.active_kind("account-1") is None
