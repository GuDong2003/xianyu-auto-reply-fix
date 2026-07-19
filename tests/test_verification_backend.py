from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from xianyu_connector.application.verification_session_manager import (
    InvalidVerificationToken,
    VerificationSessionConflict,
    VerificationSessionManager,
)
from xianyu_connector.domain.verification_session import (
    InvalidVerificationTransition,
    VerificationSession,
    VerificationSessionState,
)
from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_connector.infrastructure.sqlite_verification_repository import (
    SqliteVerificationRepository,
)
from xianyu_connector.security.aes_gcm import SecretCipher


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "verification.db"
    with sqlite3.connect(path) as connection:
        apply_connector_schema(connection)
    return path


@pytest.fixture
def manager(database_path: Path) -> VerificationSessionManager:
    repository = SqliteVerificationRepository(
        database_path,
        SecretCipher(b"k" * 32),
    )
    return VerificationSessionManager(repository, ttl_seconds=600, idle_timeout_seconds=300)


def test_session_lifecycle_and_one_time_access_token(
    manager: VerificationSessionManager,
) -> None:
    created = manager.create(
        "account-1",
        "operator-1",
        "request-1",
        challenge_info="https://example.invalid/challenge?secret=hidden",
    )
    assert created.session.state is VerificationSessionState.REQUESTED
    assert created.access_token
    manager.start(created.session.session_id)
    manager.mark_waiting(created.session.session_id)
    manager.activate(created.session.session_id, created.access_token)
    manager.submit(created.session.session_id)
    manager.begin_verification(created.session.session_id)
    succeeded = manager.complete(created.session.session_id, success=True)

    assert succeeded.state is VerificationSessionState.SUCCEEDED
    assert manager.get(created.session.session_id).challenge_info.endswith("hidden")


def test_account_has_one_active_session_and_idempotent_retries(
    manager: VerificationSessionManager,
) -> None:
    first = manager.create("account-1", "operator-1", "request-1")
    same = manager.create("account-1", "operator-1", "request-1")
    other_account = manager.create("account-2", "operator-1", "request-1")

    assert same.session.session_id == first.session.session_id
    assert same.access_token
    assert same.access_token != first.access_token
    assert other_account.session.session_id != first.session.session_id
    assert other_account.access_token

    manager.start(first.session.session_id)
    manager.mark_waiting(first.session.session_id)
    activated = manager.activate(first.session.session_id, same.access_token)
    assert activated.state is VerificationSessionState.OPERATOR_ACTIVE


@pytest.mark.parametrize(
    ("operator_id", "idempotency_key"),
    [
        ("operator-2", "request-1"),
        ("operator-1", "request-2"),
    ],
)
def test_active_session_rejects_mismatched_replay_scope(
    manager: VerificationSessionManager,
    operator_id: str,
    idempotency_key: str,
) -> None:
    manager.create("account-1", "operator-1", "request-1")

    with pytest.raises(VerificationSessionConflict):
        manager.create("account-1", operator_id, idempotency_key)


def test_challenge_is_encrypted_and_access_token_is_hashed(
    database_path: Path,
) -> None:
    repository = SqliteVerificationRepository(database_path, SecretCipher(b"k" * 32))
    manager = VerificationSessionManager(repository)
    result = manager.create(
        "account-1",
        "operator-1",
        "request-1",
        challenge_info="cookie=do-not-log",
    )

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT challenge_ciphertext, token_hash FROM "
            "account_verification_sessions JOIN verification_access_tokens USING (session_id)"
        ).fetchone()

    assert row is not None
    assert b"cookie=do-not-log" not in row[0]
    assert result.access_token.encode() not in row[1]


def test_expiry_and_idle_timeout_are_terminal(
    database_path: Path,
) -> None:
    repository = SqliteVerificationRepository(database_path, SecretCipher(b"k" * 32))
    manager = VerificationSessionManager(repository, ttl_seconds=10, idle_timeout_seconds=5)
    created_at = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
    result = manager.create("account-1", "operator-1", "request-1", now=created_at)

    expired = manager.expire_stale(now=created_at + timedelta(seconds=11))
    assert expired == 1
    assert manager.get(result.session.session_id).state is VerificationSessionState.EXPIRED

    replacement = manager.create(
        "account-1",
        "operator-1",
        "request-1",
        now=created_at + timedelta(seconds=12),
    )
    assert replacement.session.session_id != result.session.session_id
    assert replacement.access_token

    manager.cancel(replacement.session.session_id, now=created_at + timedelta(seconds=13))
    second_created_at = created_at + timedelta(seconds=14)
    second = manager.create(
        "account-1",
        "operator-1",
        "request-1",
        now=second_created_at,
    )
    assert second.session.session_id != replacement.session.session_id
    assert second.access_token
    manager.start(second.session.session_id, now=second_created_at)
    manager.mark_waiting(second.session.session_id, now=second_created_at)
    manager.expire_stale(now=second_created_at + timedelta(seconds=6))
    assert manager.get(second.session.session_id).state is VerificationSessionState.EXPIRED


def test_cancelled_session_releases_idempotency_key(
    manager: VerificationSessionManager,
) -> None:
    first = manager.create("account-1", "operator-1", "request-1")
    manager.cancel(first.session.session_id)

    replacement = manager.create("account-1", "operator-1", "request-1")

    assert replacement.session.session_id != first.session.session_id
    assert replacement.access_token


def test_failed_session_releases_idempotency_key(
    manager: VerificationSessionManager,
) -> None:
    first = manager.create("account-1", "operator-1", "request-1")
    manager.start(first.session.session_id)
    manager.complete(
        first.session.session_id,
        success=False,
        reason_code="browser_start_failed",
    )

    replacement = manager.create("account-1", "operator-1", "request-1")

    assert replacement.session.session_id != first.session.session_id
    assert replacement.access_token


def test_bad_token_is_rejected_without_leaking_secret(
    manager: VerificationSessionManager,
) -> None:
    result = manager.create("account-1", "operator-1", "request-1")

    with pytest.raises(InvalidVerificationToken):
        manager.activate(result.session.session_id, access_token="wrong")


def test_access_token_is_not_consumed_before_operator_window(
    manager: VerificationSessionManager,
) -> None:
    result = manager.create("account-1", "operator-1", "request-1")

    with pytest.raises(InvalidVerificationToken):
        manager.activate(result.session.session_id, result.access_token)

    manager.start(result.session.session_id)
    manager.mark_waiting(result.session.session_id)
    assert manager.activate(result.session.session_id, result.access_token).state is (
        VerificationSessionState.OPERATOR_ACTIVE
    )


def test_invalid_domain_transition_is_rejected() -> None:
    session = VerificationSessionManager.new_session(
        "session-1",
        "account-1",
        "operator-1",
        datetime.now(UTC),
        datetime.now(UTC) + timedelta(minutes=10),
    )

    with pytest.raises(InvalidVerificationTransition):
        session.transition(VerificationSessionState.SUCCEEDED)


def test_audit_records_every_state_transition(manager: VerificationSessionManager) -> None:
    result = manager.create("account-1", "operator-1", "request-1")
    manager.start(result.session.session_id)
    manager.mark_waiting(result.session.session_id)

    events = manager.events(result.session.session_id)
    assert [event.to_state for event in events] == [
        VerificationSessionState.REQUESTED,
        VerificationSessionState.STARTING,
        VerificationSessionState.WAITING_FOR_OPERATOR,
    ]


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("ttl_seconds", 0),
        ("idle_timeout_seconds", -1),
        ("replay_token_ttl_seconds", 0),
    ],
)
def test_manager_rejects_nonpositive_timeouts(
    database_path: Path,
    option: str,
    value: int,
) -> None:
    repository = SqliteVerificationRepository(database_path, SecretCipher(b"k" * 32))

    with pytest.raises(ValueError, match="TTLs must be positive"):
        VerificationSessionManager(repository, **{option: value})


def test_create_requires_a_nonblank_idempotency_key(
    manager: VerificationSessionManager,
) -> None:
    with pytest.raises(ValueError, match="idempotency_key is required"):
        manager.create("account-1", "operator-1", "   ")


def test_request_preserves_create_semantics(manager: VerificationSessionManager) -> None:
    requested = manager.request(
        "account-1",
        7,
        "request-via-alias",
        challenge_info="https://example.invalid/challenge",
    )

    assert requested.created is True
    assert requested.session.operator_id == "7"
    assert requested.session.challenge_info == "https://example.invalid/challenge"
    assert requested.access_token


def test_create_expires_stale_active_session_before_retrying(
    database_path: Path,
) -> None:
    repository = SqliteVerificationRepository(database_path, SecretCipher(b"k" * 32))
    manager = VerificationSessionManager(repository, ttl_seconds=10, idle_timeout_seconds=300)
    created_at = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
    stale = manager.create("account-1", "operator-1", "stale", now=created_at)

    replacement = manager.create(
        "account-1",
        "operator-2",
        "replacement",
        now=created_at + timedelta(seconds=11),
    )

    assert replacement.created is True
    assert replacement.session.session_id != stale.session.session_id
    assert manager.get(stale.session.session_id).state is VerificationSessionState.EXPIRED


def _session_in_state(
    state: VerificationSessionState,
    *,
    idempotency_key: str = "race-key",
) -> VerificationSession:
    now = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
    session = VerificationSessionManager.new_session(
        "session-from-race",
        "account-1",
        "operator-1",
        now,
        now + timedelta(minutes=10),
        idempotency_key=idempotency_key,
    )
    path = {
        VerificationSessionState.WAITING_FOR_OPERATOR: (
            VerificationSessionState.STARTING,
            VerificationSessionState.WAITING_FOR_OPERATOR,
        ),
        VerificationSessionState.CANCELLED: (VerificationSessionState.CANCELLED,),
    }[state]
    for target in path:
        session = session.transition(target, now=now)
    return session


def test_integrity_race_replays_same_scope_with_a_fresh_token() -> None:
    repository = Mock()
    existing = _session_in_state(VerificationSessionState.WAITING_FOR_OPERATOR)
    repository.get_by_idempotency.side_effect = [None, existing]
    repository.get_active_for_account.side_effect = [None, existing]
    repository.create_with_access_token.side_effect = RuntimeError(
        "UNIQUE constraint failed during concurrent insert"
    )
    manager = VerificationSessionManager(repository)
    now = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)

    replay = manager.create(
        "account-1",
        "operator-1",
        "race-key",
        now=now,
    )

    assert replay.created is False
    assert replay.session is existing
    assert replay.access_token
    repository.create_access_token.assert_called_once()


def test_integrity_race_reports_conflicting_active_scope() -> None:
    repository = Mock()
    active = _session_in_state(
        VerificationSessionState.WAITING_FOR_OPERATOR,
        idempotency_key="other-key",
    )
    repository.get_by_idempotency.side_effect = [None, None]
    repository.get_active_for_account.side_effect = [None, active]
    repository.create_with_access_token.side_effect = sqlite3.IntegrityError(
        "active account uniqueness conflict"
    )
    manager = VerificationSessionManager(repository)
    now = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)

    with pytest.raises(VerificationSessionConflict) as conflict:
        manager.create("account-1", "operator-1", "race-key", now=now)

    assert conflict.value.__cause__ is not None


def test_terminal_idempotency_replay_returns_no_new_token() -> None:
    repository = Mock()
    terminal = _session_in_state(VerificationSessionState.CANCELLED)
    repository.get_by_idempotency.return_value = terminal
    manager = VerificationSessionManager(repository)

    replay = manager.create(
        "account-1",
        "operator-1",
        "race-key",
        now=datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
    )

    assert replay == type(replay)(terminal, "", False)
    repository.create_access_token.assert_not_called()


def test_account_scope_terminal_authentication_and_touch_are_idempotent(
    manager: VerificationSessionManager,
) -> None:
    created = manager.create("account-1", "operator-1", "terminal-scope")

    with pytest.raises(LookupError):
        manager.get(created.session.session_id, account_id="account-2")

    cancelled = manager.cancel(created.session.session_id)
    assert manager.authenticate(created.session.session_id, created.access_token) is False
    touched = manager.touch(created.session.session_id)
    assert touched.session_id == cancelled.session_id
    assert touched.state is VerificationSessionState.CANCELLED
    assert touched.last_activity_at == cancelled.last_activity_at


def test_completion_notifies_recovery_and_runtime_ports(database_path: Path) -> None:
    repository = SqliteVerificationRepository(database_path, SecretCipher(b"k" * 32))
    runtime_port = Mock()
    recovery_port = Mock()
    manager = VerificationSessionManager(
        repository,
        runtime_port=runtime_port,
        recovery_port=recovery_port,
    )

    succeeded = manager.create("account-1", "operator-1", "success")
    manager.start(succeeded.session.session_id)
    manager.mark_waiting(succeeded.session.session_id)
    manager.submit(succeeded.session.session_id)
    manager.begin_verification(succeeded.session.session_id)
    manager.complete(succeeded.session.session_id, success=True)

    failed = manager.create("account-2", "operator-1", "failure")
    manager.start(failed.session.session_id)
    manager.complete(failed.session.session_id, success=False)

    recovery_port.resume_after_verification.assert_called_once_with(
        "account-1",
        succeeded.session.session_id,
    )
    runtime_port.require_manual_verification.assert_called_once_with(
        "verification_failed",
        "人工验证未通过",
    )


def test_manual_device_requirement_is_terminal(manager: VerificationSessionManager) -> None:
    created = manager.create("account-1", "operator-1", "manual-device")
    manager.start(created.session.session_id)

    required = manager.require_manual_device(created.session.session_id)

    assert required.state is VerificationSessionState.MANUAL_DEVICE_REQUIRED
    assert required.terminal is True
    assert required.reason_code == "manual_device_required"


def test_integrity_race_returns_terminal_same_scope_without_token() -> None:
    repository = Mock()
    terminal = _session_in_state(VerificationSessionState.CANCELLED)
    repository.get_by_idempotency.side_effect = [None, terminal]
    repository.get_active_for_account.side_effect = [None, None]
    repository.create_with_access_token.side_effect = sqlite3.IntegrityError(
        "concurrent terminal insert"
    )
    manager = VerificationSessionManager(repository)

    replay = manager.create(
        "account-1",
        "operator-1",
        "race-key",
        now=datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
    )

    assert replay.session is terminal
    assert replay.access_token == ""
    assert replay.created is False


def test_non_integrity_insert_failure_is_not_misclassified() -> None:
    repository = Mock()
    repository.get_by_idempotency.return_value = None
    repository.get_active_for_account.return_value = None
    repository.create_with_access_token.side_effect = RuntimeError("disk unavailable")
    manager = VerificationSessionManager(repository)

    with pytest.raises(RuntimeError, match="disk unavailable"):
        manager.create("account-1", "operator-1", "request-1")


def test_replay_token_rejects_session_at_expiry_boundary() -> None:
    repository = Mock()
    manager = VerificationSessionManager(repository)
    current_time = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
    session = VerificationSessionManager.new_session(
        "session-expiring-now",
        "account-1",
        "operator-1",
        current_time - timedelta(minutes=1),
        current_time,
        idempotency_key="boundary",
    )

    with pytest.raises(VerificationSessionConflict, match="no longer active"):
        manager._issue_replay_token(session, current_time)

    repository.create_access_token.assert_not_called()


def test_active_lookup_and_missing_session_are_explicit(
    manager: VerificationSessionManager,
) -> None:
    assert manager.active_for_account("missing-account") is None

    with pytest.raises(LookupError):
        manager.get("missing-session")
