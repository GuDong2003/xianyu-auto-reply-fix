from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum


class AccountState(StrEnum):
    DISABLED = "disabled"
    OFFLINE = "offline"
    QR_PENDING = "qr_pending"
    AUTHENTICATING = "authenticating"
    CONNECTING = "connecting"
    ONLINE = "online"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    MANUAL_VERIFICATION_REQUIRED = "manual_verification_required"
    PAUSED = "paused"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AccountReadiness:
    session_ready: bool = False
    token_ready: bool = False
    websocket_ready: bool = False
    stream_ready: bool = False

    @property
    def online(self) -> bool:
        return all(
            (
                self.session_ready,
                self.token_ready,
                self.websocket_ready,
                self.stream_ready,
            )
        )


@dataclass(frozen=True, slots=True)
class AccountRuntime:
    account_id: str
    state: AccountState = AccountState.OFFLINE
    readiness: AccountReadiness = AccountReadiness()
    version: int = 0
    reason_code: str | None = None
    reason_message: str | None = None
    entered_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=UTC))
    last_heartbeat_ack_at: datetime | None = None
    last_session_keepalive_at: datetime | None = None
    last_business_message_at: datetime | None = None
    next_action_at: datetime | None = None
    worker_heartbeat_at: datetime | None = None
    worker_pid: int | None = None
    profile_generation: int = 1
    restart_count: int = 0


class InvalidAccountTransition(ValueError):
    pass


_ALLOWED_TRANSITIONS: dict[AccountState, frozenset[AccountState]] = {
    AccountState.DISABLED: frozenset({AccountState.OFFLINE}),
    AccountState.OFFLINE: frozenset(
        {AccountState.DISABLED, AccountState.QR_PENDING, AccountState.AUTHENTICATING}
    ),
    AccountState.QR_PENDING: frozenset(
        {
            AccountState.AUTHENTICATING,
            AccountState.MANUAL_VERIFICATION_REQUIRED,
            AccountState.OFFLINE,
            AccountState.FAILED,
        }
    ),
    AccountState.AUTHENTICATING: frozenset(
        {
            AccountState.CONNECTING,
            AccountState.MANUAL_VERIFICATION_REQUIRED,
            AccountState.PAUSED,
            AccountState.FAILED,
            AccountState.OFFLINE,
        }
    ),
    AccountState.CONNECTING: frozenset(
        {
            AccountState.ONLINE,
            AccountState.DEGRADED,
            AccountState.RECOVERING,
            AccountState.MANUAL_VERIFICATION_REQUIRED,
            AccountState.FAILED,
            AccountState.OFFLINE,
        }
    ),
    AccountState.ONLINE: frozenset(
        {AccountState.DEGRADED, AccountState.PAUSED, AccountState.OFFLINE}
    ),
    AccountState.DEGRADED: frozenset(
        {
            AccountState.RECOVERING,
            AccountState.ONLINE,
            AccountState.MANUAL_VERIFICATION_REQUIRED,
            AccountState.FAILED,
            AccountState.OFFLINE,
        }
    ),
    AccountState.RECOVERING: frozenset(
        {
            AccountState.CONNECTING,
            AccountState.ONLINE,
            AccountState.MANUAL_VERIFICATION_REQUIRED,
            AccountState.PAUSED,
            AccountState.FAILED,
            AccountState.OFFLINE,
        }
    ),
    AccountState.MANUAL_VERIFICATION_REQUIRED: frozenset(
        {AccountState.QR_PENDING, AccountState.AUTHENTICATING, AccountState.PAUSED}
    ),
    AccountState.PAUSED: frozenset(
        {AccountState.OFFLINE, AccountState.QR_PENDING, AccountState.AUTHENTICATING}
    ),
    AccountState.FAILED: frozenset(
        {AccountState.OFFLINE, AccountState.QR_PENDING, AccountState.DISABLED}
    ),
}


def transition_account(
    runtime: AccountRuntime,
    target: AccountState,
    *,
    readiness: AccountReadiness | None = None,
    reason_code: str | None = None,
    reason_message: str | None = None,
    occurred_at: datetime | None = None,
) -> AccountRuntime:
    next_readiness = readiness or runtime.readiness
    if target not in _ALLOWED_TRANSITIONS[runtime.state]:
        raise InvalidAccountTransition(f"{runtime.state.value} -> {target.value}")
    if target is AccountState.ONLINE and not next_readiness.online:
        raise InvalidAccountTransition("online requires all readiness checks")

    return replace(
        runtime,
        state=target,
        readiness=next_readiness,
        version=runtime.version + 1,
        reason_code=reason_code,
        reason_message=reason_message,
        entered_at=occurred_at or datetime.now(UTC),
    )


def find_transition_path(source: AccountState, target: AccountState) -> tuple[AccountState, ...]:
    if source is target:
        return ()
    queue: list[tuple[AccountState, tuple[AccountState, ...]]] = [(source, ())]
    visited = {source}
    while queue:
        state, path = queue.pop(0)
        for candidate in _ALLOWED_TRANSITIONS[state]:
            if candidate in visited:
                continue
            next_path = (*path, candidate)
            if candidate is target:
                return next_path
            visited.add(candidate)
            queue.append((candidate, next_path))
    raise InvalidAccountTransition(f"no path from {source.value} to {target.value}")
