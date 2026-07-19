from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum


class VerificationSessionState(StrEnum):
    REQUESTED = "requested"
    STARTING = "starting"
    WAITING_FOR_OPERATOR = "waiting_for_operator"
    OPERATOR_ACTIVE = "operator_active"
    SUBMITTED = "submitted"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    MANUAL_DEVICE_REQUIRED = "manual_device_required"


class InvalidVerificationTransition(ValueError):
    """Raised when a verification session leaves its explicit state machine."""


_TERMINAL_STATES = frozenset(
    {
        VerificationSessionState.SUCCEEDED,
        VerificationSessionState.FAILED,
        VerificationSessionState.EXPIRED,
        VerificationSessionState.CANCELLED,
        VerificationSessionState.MANUAL_DEVICE_REQUIRED,
    }
)
_ALLOWED_TRANSITIONS: dict[
    VerificationSessionState, frozenset[VerificationSessionState]
] = {
    VerificationSessionState.REQUESTED: frozenset(
        {VerificationSessionState.STARTING, VerificationSessionState.EXPIRED,
         VerificationSessionState.CANCELLED}
    ),
    VerificationSessionState.STARTING: frozenset(
        {VerificationSessionState.WAITING_FOR_OPERATOR, VerificationSessionState.FAILED,
         VerificationSessionState.EXPIRED, VerificationSessionState.CANCELLED,
         VerificationSessionState.MANUAL_DEVICE_REQUIRED}
    ),
    VerificationSessionState.WAITING_FOR_OPERATOR: frozenset(
        {VerificationSessionState.OPERATOR_ACTIVE, VerificationSessionState.SUBMITTED,
         VerificationSessionState.EXPIRED, VerificationSessionState.CANCELLED,
         VerificationSessionState.MANUAL_DEVICE_REQUIRED}
    ),
    VerificationSessionState.OPERATOR_ACTIVE: frozenset(
        {VerificationSessionState.SUBMITTED, VerificationSessionState.EXPIRED,
         VerificationSessionState.CANCELLED, VerificationSessionState.FAILED,
         VerificationSessionState.MANUAL_DEVICE_REQUIRED}
    ),
    VerificationSessionState.SUBMITTED: frozenset(
        {VerificationSessionState.VERIFYING, VerificationSessionState.FAILED,
         VerificationSessionState.EXPIRED, VerificationSessionState.MANUAL_DEVICE_REQUIRED}
    ),
    VerificationSessionState.VERIFYING: frozenset(
        {VerificationSessionState.SUCCEEDED, VerificationSessionState.FAILED,
         VerificationSessionState.EXPIRED, VerificationSessionState.MANUAL_DEVICE_REQUIRED}
    ),
    VerificationSessionState.SUCCEEDED: frozenset(),
    VerificationSessionState.FAILED: frozenset(),
    VerificationSessionState.EXPIRED: frozenset(),
    VerificationSessionState.CANCELLED: frozenset(),
    VerificationSessionState.MANUAL_DEVICE_REQUIRED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class VerificationSession:
    session_id: str
    account_id: str
    operator_id: str
    state: VerificationSessionState
    created_at: datetime
    expires_at: datetime
    last_activity_at: datetime
    version: int = 0
    reason_code: str | None = None
    reason_message: str | None = None
    worker_pid: int | None = None
    completed_at: datetime | None = None
    challenge_info: str | None = None
    idempotency_key: str = ""

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    @property
    def active(self) -> bool:
        return not self.terminal

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def expired_at(self, now: datetime) -> bool:
        return now >= self.expires_at

    def idle_expired_at(self, now: datetime, idle_timeout_seconds: int) -> bool:
        elapsed = (now - self.last_activity_at).total_seconds()
        return elapsed >= idle_timeout_seconds

    def touch(self, now: datetime) -> VerificationSession:
        if self.terminal:
            raise InvalidVerificationTransition("terminal session cannot be touched")
        return replace(self, last_activity_at=now)

    def transition(
        self,
        target: VerificationSessionState,
        *,
        now: datetime | None = None,
        reason_code: str | None = None,
        reason_message: str | None = None,
        worker_pid: int | None = None,
    ) -> VerificationSession:
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise InvalidVerificationTransition(f"{self.state.value} -> {target.value}")
        occurred_at = now or datetime.now(UTC)
        completed_at = occurred_at if target in _TERMINAL_STATES else self.completed_at
        return replace(
            self,
            state=target,
            version=self.version + 1,
            last_activity_at=occurred_at,
            reason_code=reason_code,
            reason_message=reason_message,
            worker_pid=worker_pid if worker_pid is not None else self.worker_pid,
            completed_at=completed_at,
        )


def active_state(state: VerificationSessionState) -> bool:
    return state not in _TERMINAL_STATES


@dataclass(frozen=True, slots=True)
class VerificationEvent:
    session_id: str
    account_id: str
    from_state: VerificationSessionState | None
    to_state: VerificationSessionState
    event_type: str
    occurred_at: datetime
    reason_code: str | None = None
    reason_message: str | None = None
