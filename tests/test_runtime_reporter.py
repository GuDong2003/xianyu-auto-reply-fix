import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from xianyu_connector.application.runtime_reporter import RuntimeReporter, _merge_observation
from xianyu_connector.domain.account_state import AccountRuntime, AccountState, transition_account
from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository


def _repository(tmp_path: Path) -> SqliteRuntimeRepository:
    database_path = tmp_path / "runtime.db"
    with sqlite3.connect(database_path) as connection:
        apply_connector_schema(connection)
    return SqliteRuntimeRepository(database_path)


def test_reporter_marks_online_only_after_four_checks(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    initial = repository.ensure("account-1")
    authenticating = transition_account(initial, AccountState.AUTHENTICATING)
    repository.save_transition(initial, authenticating)
    reporter = RuntimeReporter("account-1", repository)

    reporter.mark_session(True)
    reporter.mark_token(True)
    connecting = reporter.mark_websocket(True, worker_pid=123)

    assert connecting.state is AccountState.CONNECTING
    online = reporter.mark_heartbeat(datetime(2026, 7, 17, tzinfo=UTC))
    assert online.state is AccountState.ONLINE
    assert online.readiness.online is True


def test_lost_readiness_degrades_online_account(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    initial = repository.ensure("account-1")
    authenticating = transition_account(initial, AccountState.AUTHENTICATING)
    repository.save_transition(initial, authenticating)
    reporter = RuntimeReporter("account-1", repository)
    reporter.mark_session(True)
    reporter.mark_token(True)
    reporter.mark_websocket(True)
    reporter.mark_heartbeat()

    degraded = reporter.mark_websocket(False)

    assert degraded.state is AccountState.DEGRADED
    assert degraded.readiness.online is False


def test_risk_challenge_clears_readiness_and_requires_manual_action(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    reporter = RuntimeReporter("account-1", repository)

    runtime = reporter.require_manual_verification("risk_challenge", "manual verification")

    assert runtime.state is AccountState.MANUAL_VERIFICATION_REQUIRED
    assert runtime.readiness.online is False


def test_shadow_mode_blocks_actions_even_when_account_is_online(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    reporter = RuntimeReporter("account-1", repository, shadow_mode=True)
    initial = repository.ensure("account-1")
    authenticating = transition_account(initial, AccountState.AUTHENTICATING)
    repository.save_transition(initial, authenticating)
    reporter.mark_session(True)
    reporter.mark_token(True)
    reporter.mark_websocket(True)
    reporter.mark_heartbeat()

    assert reporter.actions_allowed() is False


def test_reporter_records_business_and_worker_activity(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    reporter = RuntimeReporter("account-1", repository)
    business_at = datetime(2026, 7, 17, tzinfo=UTC)

    reporter.mark_business_message(business_at)
    runtime = reporter.mark_worker_heartbeat(321, business_at)

    assert runtime.last_business_message_at == business_at
    assert runtime.worker_heartbeat_at == business_at
    assert runtime.worker_pid == 321


def test_online_manual_verification_degrades_then_stops_actions(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    initial = repository.ensure("account-1")
    repository.save_transition(initial, transition_account(initial, AccountState.AUTHENTICATING))
    reporter = RuntimeReporter("account-1", repository, shadow_mode=False)
    reporter.mark_session(True)
    reporter.mark_token(True)
    reporter.mark_websocket(True)
    reporter.mark_heartbeat()
    assert reporter.actions_allowed() is True

    runtime = reporter.require_manual_verification("risk_challenge", "manual")

    assert runtime.state is AccountState.MANUAL_VERIFICATION_REQUIRED
    assert reporter.actions_allowed() is False


@pytest.mark.parametrize(
    "changes",
    [
        {"session_ready": "yes"},
        {"last_heartbeat_ack_at": "now"},
        {"worker_pid": True},
    ],
)
def test_runtime_observations_reject_invalid_types(changes: dict[str, object]) -> None:
    with pytest.raises(TypeError):
        _merge_observation(AccountRuntime("account-1"), changes)
