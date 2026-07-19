from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

from xianyu_connector.application.account_operation_coordinator import (
    AccountOperationConflict,
    AccountOperationCoordinator,
    AccountOperationKind,
)
from xianyu_connector.application.runtime_service import RuntimeService
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.domain.commands import AccountCommand, CommandRecord
from xianyu_connector.domain.recovery_policy import FailureKind, decide_recovery
from xianyu_connector.infrastructure.sqlite_command_repository import SqliteCommandRepository
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository
from xianyu_connector.infrastructure.worker_process import WorkerExitCode

logger = logging.getLogger(__name__)


class WorkerHandle(Protocol):
    @property
    def pid(self) -> int: ...

    def is_alive(self) -> bool: ...

    def return_code(self) -> int | None: ...

    def stop(self, grace_seconds: float = 10) -> None: ...


class WorkerFactory(Protocol):
    def start(self, account_id: str) -> WorkerHandle: ...


class AccountSupervisor:
    def __init__(
        self,
        runtime_repository: SqliteRuntimeRepository,
        command_repository: SqliteCommandRepository,
        worker_factory: WorkerFactory,
        *,
        coordinator: AccountOperationCoordinator | None = None,
    ) -> None:
        self._runtime_repository = runtime_repository
        self._command_repository = command_repository
        self._worker_factory = worker_factory
        self._coordinator = coordinator or AccountOperationCoordinator()
        self._runtime_service = RuntimeService(runtime_repository)
        self._workers: dict[str, WorkerHandle] = {}

    def process_next_command(self) -> CommandRecord | None:
        command = self._command_repository.claim_next()
        if not command:
            return None
        try:
            with self._coordinator.hold(command.account_id):
                result = self._execute_command(command)
            self._command_repository.complete(command.command_id, result=result)
        except Exception as exc:
            self._command_repository.complete(command.command_id, error_message=str(exc))
        return self._command_repository.get(command.command_id)

    def supervise(self, now: datetime | None = None) -> None:
        current_time = now or datetime.now(UTC)
        self._stop_stale_workers(current_time)
        self._record_exited_workers(current_time)
        self._start_due_recoveries(current_time)

    def stop_all(self) -> None:
        for worker in tuple(self._workers.values()):
            worker.stop()
        self._workers.clear()

    def _execute_command(self, record: CommandRecord) -> dict[str, object]:
        active_operation = self._coordinator.active_kind(record.account_id)
        if active_operation is not None and not (
            record.command is AccountCommand.RELOGIN_QR
            and active_operation is AccountOperationKind.QR
        ):
            raise AccountOperationConflict(
                f"{active_operation.value} operation is active for {record.account_id}"
            )
        if record.command is AccountCommand.START:
            return self._start_account(record.account_id)
        if record.command is AccountCommand.STOP:
            return self._stop_account(record.account_id)
        if record.command is AccountCommand.RECONNECT:
            self._stop_account(record.account_id)
            return self._start_account(record.account_id)
        if record.command is AccountCommand.RELOGIN_QR:
            self._stop_worker(record.account_id)
            runtime = self._runtime_service.transition_to(
                record.account_id,
                AccountState.QR_PENDING,
                clear_readiness=True,
            )
            observed = replace(
                runtime,
                worker_pid=None,
                next_action_at=None,
                restart_count=0,
            )
            self._runtime_repository.save_observation(runtime, observed)
            return {"state": observed.state.value}
        if record.command is AccountCommand.RESUME_AFTER_VERIFICATION:
            return self._start_account(record.account_id)
        raise ValueError(f"unsupported command: {record.command}")

    def _start_account(self, account_id: str) -> dict[str, object]:
        worker = self._workers.get(account_id)
        if worker and worker.is_alive():
            return {"state": "already_running", "worker_pid": worker.pid}
        runtime = self._runtime_service.transition_to(
            account_id,
            AccountState.AUTHENTICATING,
            clear_readiness=True,
        )
        try:
            worker = self._worker_factory.start(account_id)
        except Exception:
            self._converge_worker_failure(
                account_id,
                reason_code="worker_start_failed",
                reason_message="账号连接进程启动失败",
            )
            raise
        self._workers[account_id] = worker
        observed = replace(
            runtime,
            worker_pid=worker.pid,
            worker_heartbeat_at=datetime.now(UTC),
            next_action_at=None,
        )
        try:
            self._runtime_repository.save_observation(runtime, observed)
        except Exception:
            try:
                worker.stop()
            finally:
                self._workers.pop(account_id, None)
                self._converge_worker_failure(
                    account_id,
                    reason_code="worker_start_failed",
                    reason_message="账号连接进程启动状态保存失败",
                )
            raise
        return {"state": observed.state.value, "worker_pid": worker.pid}

    def _stop_account(self, account_id: str) -> dict[str, object]:
        self._stop_worker(account_id)
        runtime = self._runtime_service.transition_to(
            account_id,
            AccountState.OFFLINE,
            reason_code="operator_stop",
            clear_readiness=True,
        )
        observed = replace(runtime, worker_pid=None, next_action_at=None)
        self._runtime_repository.save_observation(runtime, observed)
        return {"state": observed.state.value}

    def _stop_worker(self, account_id: str) -> None:
        worker = self._workers.get(account_id)
        if worker:
            worker.stop()
            self._workers.pop(account_id, None)

    def _record_exited_workers(self, now: datetime) -> None:
        for account_id, worker in tuple(self._workers.items()):
            if worker.is_alive():
                continue
            self._workers.pop(account_id, None)
            self._handle_worker_exit(account_id, worker.return_code(), now)

    def _stop_stale_workers(self, now: datetime) -> None:
        stale_before = now - timedelta(seconds=30)
        for account_id, worker in tuple(self._workers.items()):
            if not worker.is_alive():
                continue
            runtime = self._runtime_repository.get(account_id)
            if not runtime or not runtime.worker_heartbeat_at:
                continue
            if runtime.worker_heartbeat_at >= stale_before:
                continue
            try:
                worker.stop()
            except Exception:
                self._converge_worker_failure(
                    account_id,
                    reason_code="worker_stop_failed",
                    reason_message="账号连接进程停止失败",
                )
                continue
            self._workers.pop(account_id, None)
            self._handle_worker_exit(account_id, WorkerExitCode.FATAL, now)

    def _handle_worker_exit(self, account_id: str, return_code: int | None, now: datetime) -> None:
        failure = _failure_from_exit_code(return_code)
        runtime = self._runtime_repository.ensure(account_id)
        decision = decide_recovery(failure, runtime.restart_count)
        target = decision.target_state
        transitioned = self._runtime_service.transition_to(
            account_id,
            target,
            reason_code=decision.reason_code,
            clear_readiness=True,
        )
        next_action = (
            now + timedelta(seconds=decision.retry_after_seconds or 0)
            if decision.allow_automatic_retry
            else None
        )
        observed = replace(
            transitioned,
            worker_pid=None,
            restart_count=runtime.restart_count + 1,
            next_action_at=next_action,
        )
        self._runtime_repository.save_observation(transitioned, observed)

    def _start_due_recoveries(self, now: datetime) -> None:
        for runtime in self._runtime_repository.list_all():
            if runtime.state is not AccountState.RECOVERING:
                continue
            if runtime.next_action_at and runtime.next_action_at > now:
                continue
            if runtime.account_id in self._workers:
                continue
            try:
                with self._coordinator.hold(runtime.account_id):
                    if self._coordinator.active_kind(runtime.account_id) is not None:
                        continue
                    self._start_account(runtime.account_id)
            except AccountOperationConflict:
                continue
            except Exception:
                logger.exception(
                    "automatic account recovery start failed",
                    extra={"account_id": runtime.account_id},
                )
                continue

    def _converge_worker_failure(
        self,
        account_id: str,
        *,
        reason_code: str,
        reason_message: str,
    ) -> None:
        self._runtime_repository.ensure(account_id)
        failed = self._runtime_service.transition_to(
            account_id,
            AccountState.FAILED,
            reason_code=reason_code,
            reason_message=reason_message,
            clear_readiness=True,
        )
        observed = replace(
            failed,
            worker_pid=None,
            worker_heartbeat_at=None,
            next_action_at=None,
        )
        self._runtime_repository.save_observation(failed, observed)


def _failure_from_exit_code(return_code: int | None) -> FailureKind:
    if return_code == WorkerExitCode.MANUAL_VERIFICATION:
        return FailureKind.RISK_CHALLENGE
    if return_code == WorkerExitCode.SESSION_EXPIRED:
        return FailureKind.SESSION_EXPIRED
    if return_code == WorkerExitCode.RISK_CHALLENGE:
        return FailureKind.RISK_CHALLENGE
    if return_code == WorkerExitCode.NETWORK_FAILURE:
        return FailureKind.NETWORK
    if return_code == WorkerExitCode.TOKEN_EXPIRED:
        return FailureKind.TOKEN_EXPIRED
    if return_code in {WorkerExitCode.STOPPED, None}:
        return FailureKind.UNKNOWN
    return FailureKind.PROCESS_CRASH
