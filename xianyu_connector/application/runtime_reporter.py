from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock

from xianyu_connector.domain.account_state import (
    AccountReadiness,
    AccountRuntime,
    AccountState,
    transition_account,
)
from xianyu_connector.infrastructure.sqlite_runtime_repository import (
    RuntimeVersionConflict,
    SqliteRuntimeRepository,
)


class RuntimeReporter:
    def __init__(
        self,
        account_id: str,
        repository: SqliteRuntimeRepository,
        *,
        shadow_mode: bool = True,
    ) -> None:
        self._account_id = account_id
        self._repository = repository
        self._shadow_mode = shadow_mode
        self._lock = RLock()

    def actions_allowed(self) -> bool:
        runtime = self._repository.get(self._account_id)
        return bool(not self._shadow_mode and runtime and runtime.readiness.online)

    def mark_session(self, ready: bool, occurred_at: datetime | None = None) -> AccountRuntime:
        timestamp = occurred_at or datetime.now(UTC)
        return self._update_readiness(
            session_ready=ready,
            last_session_keepalive_at=timestamp if ready else None,
        )

    def mark_token(self, ready: bool) -> AccountRuntime:
        return self._update_readiness(token_ready=ready)

    def mark_websocket(self, ready: bool, *, worker_pid: int | None = None) -> AccountRuntime:
        return self._update_readiness(websocket_ready=ready, worker_pid=worker_pid)

    def mark_heartbeat(self, occurred_at: datetime | None = None) -> AccountRuntime:
        timestamp = occurred_at or datetime.now(UTC)
        return self._update_readiness(stream_ready=True, last_heartbeat_ack_at=timestamp)

    def mark_business_message(self, occurred_at: datetime | None = None) -> AccountRuntime:
        timestamp = occurred_at or datetime.now(UTC)
        return self._update_readiness(stream_ready=True, last_business_message_at=timestamp)

    def mark_worker_heartbeat(
        self,
        worker_pid: int,
        occurred_at: datetime | None = None,
    ) -> AccountRuntime:
        return self._update_readiness(
            worker_heartbeat_at=occurred_at or datetime.now(UTC),
            worker_pid=worker_pid,
        )

    def require_manual_verification(self, reason_code: str, message: str) -> AccountRuntime:
        with self._lock:
            runtime = self._repository.ensure(self._account_id)
            for target in _path_to_manual(runtime.state):
                current = transition_account(
                    runtime,
                    target,
                    readiness=AccountReadiness(),
                    reason_code=reason_code,
                    reason_message=message,
                )
                self._repository.save_transition(runtime, current)
                runtime = current
            return runtime

    def _update_readiness(self, **changes: object) -> AccountRuntime:
        with self._lock:
            for _ in range(3):
                runtime = self._repository.ensure(self._account_id)
                current = _merge_observation(runtime, changes)
                try:
                    self._persist(runtime, current)
                    return current
                except RuntimeVersionConflict:
                    continue
        raise RuntimeVersionConflict(self._account_id)

    def _persist(self, previous: AccountRuntime, current: AccountRuntime) -> None:
        if current.version == previous.version:
            self._repository.save_observation(previous, current)
            return
        self._repository.save_transition(previous, current)


def _merge_observation(runtime: AccountRuntime, changes: dict[str, object]) -> AccountRuntime:
    readiness = AccountReadiness(
        session_ready=_bool_change(changes, "session_ready", runtime.readiness.session_ready),
        token_ready=_bool_change(changes, "token_ready", runtime.readiness.token_ready),
        websocket_ready=_bool_change(
            changes,
            "websocket_ready",
            runtime.readiness.websocket_ready,
        ),
        stream_ready=_bool_change(changes, "stream_ready", runtime.readiness.stream_ready),
    )
    observed = replace(
        runtime,
        readiness=readiness,
        last_heartbeat_ack_at=_datetime_change(
            changes,
            "last_heartbeat_ack_at",
            runtime.last_heartbeat_ack_at,
        ),
        last_session_keepalive_at=_datetime_change(
            changes,
            "last_session_keepalive_at",
            runtime.last_session_keepalive_at,
        ),
        last_business_message_at=_datetime_change(
            changes,
            "last_business_message_at",
            runtime.last_business_message_at,
        ),
        worker_heartbeat_at=_datetime_change(
            changes,
            "worker_heartbeat_at",
            runtime.worker_heartbeat_at,
        ),
        worker_pid=_integer_change(changes, "worker_pid", runtime.worker_pid),
    )
    if runtime.state is AccountState.AUTHENTICATING and readiness.websocket_ready:
        return transition_account(observed, AccountState.CONNECTING, readiness=readiness)
    if readiness.online and runtime.state in {
        AccountState.CONNECTING,
        AccountState.DEGRADED,
        AccountState.RECOVERING,
    }:
        return replace(
            transition_account(observed, AccountState.ONLINE, readiness=readiness),
            restart_count=0,
            next_action_at=None,
        )
    if runtime.state is AccountState.ONLINE and not readiness.online:
        return transition_account(observed, AccountState.DEGRADED, readiness=readiness)
    return observed


def _bool_change(changes: dict[str, object], key: str, current: bool) -> bool:
    value = changes.get(key, current)
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be bool")
    return value


def _datetime_change(
    changes: dict[str, object],
    key: str,
    current: datetime | None,
) -> datetime | None:
    value = changes.get(key, current)
    if value is not None and not isinstance(value, datetime):
        raise TypeError(f"{key} must be datetime or None")
    return value


def _integer_change(
    changes: dict[str, object],
    key: str,
    current: int | None,
) -> int | None:
    value = changes.get(key, current)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise TypeError(f"{key} must be int or None")
    return value


def _path_to_manual(state: AccountState) -> tuple[AccountState, ...]:
    if state is AccountState.MANUAL_VERIFICATION_REQUIRED:
        return ()
    if state is AccountState.ONLINE:
        return (AccountState.DEGRADED, AccountState.MANUAL_VERIFICATION_REQUIRED)
    if state is AccountState.OFFLINE:
        return (AccountState.AUTHENTICATING, AccountState.MANUAL_VERIFICATION_REQUIRED)
    if state in {AccountState.DISABLED, AccountState.FAILED}:
        return (AccountState.OFFLINE, AccountState.AUTHENTICATING, AccountState.MANUAL_VERIFICATION_REQUIRED)
    return (AccountState.MANUAL_VERIFICATION_REQUIRED,)
