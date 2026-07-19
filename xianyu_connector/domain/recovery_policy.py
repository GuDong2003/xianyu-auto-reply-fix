from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from xianyu_connector.domain.account_state import AccountState


class FailureKind(StrEnum):
    NETWORK = "network"
    TOKEN_EXPIRED = "token_expired"
    SESSION_EXPIRED = "session_expired"
    RISK_CHALLENGE = "risk_challenge"
    PROCESS_CRASH = "process_crash"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    target_state: AccountState
    retry_after_seconds: int | None
    allow_automatic_retry: bool
    reason_code: str


_NETWORK_BACKOFF_SECONDS = (5, 15, 30, 60, 300)


def decide_recovery(failure: FailureKind, attempt: int) -> RecoveryDecision:
    normalized_attempt = max(0, attempt)
    if failure is FailureKind.NETWORK:
        delay = _NETWORK_BACKOFF_SECONDS[min(normalized_attempt, 4)]
        return RecoveryDecision(AccountState.RECOVERING, delay, True, "network_retry")
    if failure is FailureKind.TOKEN_EXPIRED and normalized_attempt == 0:
        return RecoveryDecision(AccountState.RECOVERING, 0, True, "token_refresh_once")
    if failure in {
        FailureKind.TOKEN_EXPIRED,
        FailureKind.SESSION_EXPIRED,
        FailureKind.RISK_CHALLENGE,
    }:
        return RecoveryDecision(
            AccountState.MANUAL_VERIFICATION_REQUIRED,
            None,
            False,
            failure.value,
        )
    if failure is FailureKind.PROCESS_CRASH and normalized_attempt < 3:
        return RecoveryDecision(AccountState.RECOVERING, 10, True, "worker_restart")
    return RecoveryDecision(AccountState.FAILED, None, False, failure.value)
