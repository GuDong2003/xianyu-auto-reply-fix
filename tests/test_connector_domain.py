from datetime import UTC, datetime

import pytest

from xianyu_connector.domain.account_state import (
    AccountReadiness,
    AccountRuntime,
    AccountState,
    InvalidAccountTransition,
    transition_account,
)
from xianyu_connector.domain.connection_failure import (
    ConnectorNetworkFailure,
    ManualVerificationRequired,
    is_network_failure,
)
from xianyu_connector.domain.recovery_policy import FailureKind, decide_recovery


def test_online_requires_all_readiness_checks() -> None:
    runtime = AccountRuntime("account-1", state=AccountState.CONNECTING)

    with pytest.raises(InvalidAccountTransition):
        transition_account(runtime, AccountState.ONLINE)


def test_connecting_can_become_online_when_four_checks_are_ready() -> None:
    runtime = AccountRuntime("account-1", state=AccountState.CONNECTING)
    readiness = AccountReadiness(True, True, True, True)

    online = transition_account(
        runtime,
        AccountState.ONLINE,
        readiness=readiness,
        occurred_at=datetime(2026, 7, 17, tzinfo=UTC),
    )

    assert online.state is AccountState.ONLINE
    assert online.readiness.online is True
    assert online.version == 1


def test_illegal_transition_is_rejected() -> None:
    runtime = AccountRuntime("account-1", state=AccountState.DISABLED)

    with pytest.raises(InvalidAccountTransition):
        transition_account(runtime, AccountState.ONLINE)


@pytest.mark.parametrize(
    ("attempt", "delay"),
    [(0, 5), (1, 15), (2, 30), (3, 60), (4, 300), (9, 300)],
)
def test_network_recovery_uses_bounded_backoff(attempt: int, delay: int) -> None:
    decision = decide_recovery(FailureKind.NETWORK, attempt)

    assert decision.allow_automatic_retry is True
    assert decision.retry_after_seconds == delay


def test_token_refresh_is_attempted_only_once() -> None:
    first = decide_recovery(FailureKind.TOKEN_EXPIRED, 0)
    second = decide_recovery(FailureKind.TOKEN_EXPIRED, 1)

    assert first.allow_automatic_retry is True
    assert second.target_state is AccountState.MANUAL_VERIFICATION_REQUIRED
    assert second.allow_automatic_retry is False


@pytest.mark.parametrize(
    "failure",
    [FailureKind.SESSION_EXPIRED, FailureKind.RISK_CHALLENGE],
)
def test_authentication_risk_requires_manual_verification(failure: FailureKind) -> None:
    decision = decide_recovery(failure, 0)

    assert decision.target_state is AccountState.MANUAL_VERIFICATION_REQUIRED
    assert decision.retry_after_seconds is None


@pytest.mark.parametrize(
    ("status", "message"),
    [
        ("network_failed", None),
        (None, "DNS temporary failure"),
        ("failed", "connection timed out"),
    ],
)
def test_network_failure_classification(status: str | None, message: str | None) -> None:
    assert is_network_failure(status, message) is True


def test_non_network_failure_is_not_retried_as_network() -> None:
    assert is_network_failure("auth_failed", "session expired") is False


def test_manual_verification_signal_preserves_reason() -> None:
    error = ManualVerificationRequired("risk_challenge", "manual verification")

    assert error.reason_code == "risk_challenge"
    assert error.message == "manual verification"
    assert str(error) == "manual verification"


def test_network_failure_signal_preserves_message() -> None:
    error = ConnectorNetworkFailure("connection timed out")

    assert str(error) == "connection timed out"
