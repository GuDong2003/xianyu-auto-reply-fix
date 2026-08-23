from __future__ import annotations

import asyncio
import base64
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from xianyu_connector.api import create_connector_app
from xianyu_connector.application.local_verification_handoff import (
    LOCAL_HANDOFF_LAUNCH_TTL_SECONDS,
    LocalHandoffConflict,
    LocalVerificationHandoff,
)
from xianyu_connector.application.runtime_reporter import RuntimeReporter
from xianyu_connector.application.runtime_service import RuntimeService
from xianyu_connector.application.verification_session_manager import (
    VerificationSessionManager,
)
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.domain.verification_session import (
    InvalidVerificationTransition,
    VerificationSessionState,
)
from xianyu_connector.infrastructure.local_handoff_repository import (
    LocalHandoffGone,
    SqliteLocalHandoffRepository,
)
from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_connector.infrastructure.sqlite_command_repository import (
    SqliteCommandRepository,
)
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository
from xianyu_connector.infrastructure.sqlite_secret_repository import SqliteSecretRepository
from xianyu_connector.infrastructure.sqlite_verification_repository import (
    SqliteVerificationRepository,
    VerificationVersionConflict,
)
from xianyu_connector.infrastructure.verification_process import VerificationProcessSupervisor
from xianyu_connector.security.aes_gcm import SecretCipher
from xianyu_connector.settings import ConnectorSettings
from xianyu_control.accounts_router import LOCAL_HANDOFF_GRANT_TTL_SECONDS

INTERNAL_HEADERS = {"X-Connector-Token": "t" * 32}
CHALLENGE_URL = "https://challenge.goofish.com/verify?action=captcha&x5secdata=do-not-leak"


def _settings(tmp_path: Path, *, enabled: bool) -> ConnectorSettings:
    database_path = tmp_path / "api.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE cookies (id TEXT PRIMARY KEY, value TEXT, user_id INTEGER)"
        )
        connection.execute(
            "CREATE TABLE cookie_status (cookie_id TEXT PRIMARY KEY, enabled INTEGER)"
        )
        apply_connector_schema(connection)
    key_path = tmp_path / "master.key"
    key_path.write_bytes(base64.urlsafe_b64encode(b"k" * 32))
    return ConnectorSettings(
        database_path=database_path,
        profiles_root=tmp_path / "profiles",
        master_key_path=key_path,
        internal_api_token="t" * 32,
        local_verification_handoff_enabled=enabled,
    )


def _prepare_challenge(settings: ConnectorSettings, *, account_id: str = "account-1") -> None:
    RuntimeService(SqliteRuntimeRepository(settings.database_path)).transition_to(
        account_id,
        AccountState.MANUAL_VERIFICATION_REQUIRED,
    )
    secrets = SqliteSecretRepository(settings.database_path, SecretCipher(b"k" * 32))
    secrets.save(account_id, "verification_url", CHALLENGE_URL)
    secrets.save(account_id, "cookie", "unb=account-1; cookie2=server-cookie")


def _create_local_session(client: TestClient, *, operator_id: int = 7):
    return client.post(
        "/internal/accounts/account-1/local-verification-sessions",
        headers=INTERNAL_HEADERS,
        json={"user_id": operator_id, "idempotency_key": "local-handoff-1"},
    )


def _complete_local_session(client: TestClient, session_id: str, *, operator_id: int = 7):
    return client.post(
        f"/internal/accounts/account-1/local-verification-sessions/{session_id}/complete",
        headers={**INTERNAL_HEADERS, "X-Operator-Id": str(operator_id)},
        json={},
    )


def test_local_handoff_is_disabled_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=False)
    _prepare_challenge(settings)

    with TestClient(create_connector_app(settings)) as client:
        response = _create_local_session(client)

    assert response.status_code == 404
    assert CHALLENGE_URL not in response.text


def test_local_handoff_launch_ttl_is_fixed_at_60_seconds() -> None:
    assert LOCAL_HANDOFF_LAUNCH_TTL_SECONDS == 60
    assert LOCAL_HANDOFF_GRANT_TTL_SECONDS == LOCAL_HANDOFF_LAUNCH_TTL_SECONDS


def test_local_handoff_redirect_is_one_time_and_never_starts_playwright(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path, enabled=True)
    _prepare_challenge(settings)
    starts: list[object] = []

    def reject_process_start(*args: object, **kwargs: object) -> None:
        starts.append((args, kwargs))
        raise AssertionError("local handoff must not start a verification browser")

    monkeypatch.setattr(VerificationProcessSupervisor, "start", reject_process_start)

    with TestClient(create_connector_app(settings)) as client:
        created = _create_local_session(client)
        assert created.status_code == 200
        payload = created.json()
        ticket = payload["handoff_token"]
        session_id = payload["session_id"]
        assert CHALLENGE_URL not in created.text
        assert "challenge_info" not in payload

        repeated_create = _create_local_session(client)
        assert repeated_create.status_code == 200
        assert repeated_create.json()["session_id"] == session_id
        assert repeated_create.json()["handoff_token"] == ticket

        with sqlite3.connect(settings.database_path) as connection:
            encrypted_grant = connection.execute(
                "SELECT token_hash, token_ciphertext, created_at, expires_at "
                "FROM local_verification_handoff_grants "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        assert encrypted_grant is not None
        assert ticket.encode() not in encrypted_grant[0]
        assert ticket.encode() not in encrypted_grant[1]
        assert (
            datetime.fromisoformat(encrypted_grant[3]) - datetime.fromisoformat(encrypted_grant[2])
        ).total_seconds() == 60

        conflict = client.post(
            "/internal/accounts/account-1/local-verification-sessions",
            headers=INTERNAL_HEADERS,
            json={"user_id": 7, "idempotency_key": "different-local-handoff"},
        )
        assert conflict.status_code == 409

        query_ticket = client.get(
            f"/internal/accounts/account-1/local-verification-sessions/{session_id}/handoff",
            headers=INTERNAL_HEADERS,
            params={"ticket": ticket},
            follow_redirects=False,
        )
        assert query_ticket.status_code == 422

        wrong_ticket = client.get(
            f"/internal/accounts/account-1/local-verification-sessions/{session_id}/handoff",
            headers={**INTERNAL_HEADERS, "X-Verification-Ticket": f"{ticket}-wrong"},
            follow_redirects=False,
        )
        assert wrong_ticket.status_code == 401

        handoff = client.get(
            f"/internal/accounts/account-1/local-verification-sessions/{session_id}/handoff",
            headers={**INTERNAL_HEADERS, "X-Verification-Ticket": ticket},
            follow_redirects=False,
        )
        assert handoff.status_code == 302
        assert handoff.headers["location"] == CHALLENGE_URL
        assert CHALLENGE_URL not in handoff.text
        assert handoff.headers["cache-control"] == "no-store, private"

        with sqlite3.connect(settings.database_path) as connection:
            persisted = connection.execute(
                "SELECT token_hash, token_ciphertext FROM local_verification_handoff_grants "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        assert persisted is not None
        assert ticket.encode() not in persisted[0]
        assert persisted[1] is None

        replay = client.get(
            f"/internal/accounts/account-1/local-verification-sessions/{session_id}/handoff",
            headers={**INTERNAL_HEADERS, "X-Verification-Ticket": ticket},
            follow_redirects=False,
        )
        assert replay.status_code == 410
        assert "location" not in replay.headers
        assert "do-not-leak" not in replay.text

    assert starts == []


def test_complete_local_handoff_checks_operator_and_enqueues_resume_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path, enabled=True)
    _prepare_challenge(settings)
    monkeypatch.setattr(
        VerificationProcessSupervisor,
        "start",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("local handoff must not start a verification browser")
        ),
    )
    secrets = SqliteSecretRepository(settings.database_path, SecretCipher(b"k" * 32))
    original_cookie = secrets.get("account-1", "cookie")

    with TestClient(create_connector_app(settings)) as client:
        created = _create_local_session(client)
        session_id = created.json()["session_id"]
        ticket = created.json()["handoff_token"]

        before_handoff = _complete_local_session(client, session_id)
        assert before_handoff.status_code == 409

        client.get(
            f"/internal/accounts/account-1/local-verification-sessions/{session_id}/handoff",
            headers={**INTERNAL_HEADERS, "X-Verification-Ticket": ticket},
            follow_redirects=False,
        )
        wrong_operator = _complete_local_session(client, session_id, operator_id=8)
        assert wrong_operator.status_code == 403
        body_operator = client.post(
            f"/internal/accounts/account-1/local-verification-sessions/{session_id}/complete",
            headers={**INTERNAL_HEADERS, "X-Operator-Id": "7"},
            json={"operator_id": 7},
        )
        assert body_operator.status_code == 422

        completed = _complete_local_session(client, session_id)
        repeated = _complete_local_session(client, session_id)
        assert completed.status_code == 200
        assert repeated.status_code == 200
        assert completed.json()["state"] in {"submitted", "verifying"}

    with sqlite3.connect(settings.database_path) as connection:
        commands = connection.execute(
            "SELECT command, idempotency_key, payload_json FROM account_commands "
            "WHERE command = 'resume_after_verification'"
        ).fetchall()
    assert len(commands) == 1
    assert commands[0][1] == f"local-verification-resume:{session_id}"
    assert session_id in commands[0][2]
    assert secrets.get("account-1", "cookie") == original_cookie
    assert not (settings.profiles_root / "account-1").exists()


def test_local_handoff_rejects_unsupported_challenge_host(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    _prepare_challenge(settings)
    SqliteSecretRepository(settings.database_path, SecretCipher(b"k" * 32)).save(
        "account-1",
        "verification_url",
        "https://attacker.example/redirect",
    )

    with TestClient(create_connector_app(settings)) as client:
        response = _create_local_session(client)

    assert response.status_code == 400
    assert "attacker.example" not in response.text


def test_local_handoff_is_gone_if_challenge_changes_before_redirect(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    _prepare_challenge(settings)

    with TestClient(create_connector_app(settings)) as client:
        created = _create_local_session(client)
        SqliteSecretRepository(settings.database_path, SecretCipher(b"k" * 32)).save(
            "account-1",
            "verification_url",
            "https://challenge.goofish.com/verify?action=captcha&x5secdata=new",
        )
        response = client.get(
            "/internal/accounts/account-1/local-verification-sessions/"
            f"{created.json()['session_id']}/handoff",
            headers={
                **INTERNAL_HEADERS,
                "X-Verification-Ticket": created.json()["handoff_token"],
            },
            follow_redirects=False,
        )

    assert response.status_code == 410
    assert "location" not in response.headers
    assert "x5secdata" not in response.text


def test_expired_local_handoff_grant_is_gone(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    repository = SqliteLocalHandoffRepository(
        settings.database_path,
        SecretCipher(b"k" * 32),
    )
    created_at = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
    grant = repository.create_or_get(
        "account-1",
        "7",
        "expires",
        CHALLENGE_URL,
        ttl_seconds=1,
        now=created_at,
    )

    with pytest.raises(LocalHandoffGone):
        repository.consume_and_activate(
            "account-1",
            grant.session.session_id,
            grant.token,
            now=created_at + timedelta(seconds=2),
        )


def test_expired_unconsumed_handoff_allows_a_new_session(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    repository = SqliteLocalHandoffRepository(
        settings.database_path,
        SecretCipher(b"k" * 32),
    )
    created_at = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
    first = repository.create_or_get(
        "account-1",
        "7",
        "first-launch",
        CHALLENGE_URL,
        ttl_seconds=60,
        now=created_at,
    )

    second = repository.create_or_get(
        "account-1",
        "7",
        "second-launch",
        CHALLENGE_URL,
        ttl_seconds=60,
        now=created_at + timedelta(seconds=61),
    )

    assert second.session.session_id != first.session.session_id
    assert (
        repository.get_session("account-1", first.session.session_id).state
        is VerificationSessionState.EXPIRED
    )


def test_consumed_handoff_remains_completable_after_launch_ttl(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    cipher = SecretCipher(b"k" * 32)
    repository = SqliteLocalHandoffRepository(settings.database_path, cipher)
    created_at = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
    grant = repository.create_or_get(
        "account-1",
        "7",
        "complete-after-launch-window",
        CHALLENGE_URL,
        ttl_seconds=60,
        now=created_at,
    )
    activated = repository.consume_and_activate(
        "account-1",
        grant.session.session_id,
        grant.token,
        completion_ttl_seconds=600,
        now=created_at + timedelta(seconds=30),
    )
    manager = VerificationSessionManager(
        SqliteVerificationRepository(settings.database_path, cipher),
        idle_timeout_seconds=600,
    )

    submitted = manager.submit(
        activated.session_id,
        now=created_at + timedelta(seconds=91),
    )

    assert submitted.state is VerificationSessionState.SUBMITTED
    assert activated.expires_at == created_at + timedelta(seconds=630)


def test_consumed_handoff_cannot_complete_after_completion_ttl(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    cipher = SecretCipher(b"k" * 32)
    repository = SqliteLocalHandoffRepository(settings.database_path, cipher)
    created_at = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
    grant = repository.create_or_get(
        "account-1",
        "7",
        "expired-completion-window",
        CHALLENGE_URL,
        ttl_seconds=60,
        now=created_at,
    )
    activated = repository.consume_and_activate(
        "account-1",
        grant.session.session_id,
        grant.token,
        completion_ttl_seconds=600,
        now=created_at + timedelta(seconds=30),
    )
    manager = VerificationSessionManager(
        SqliteVerificationRepository(settings.database_path, cipher),
        idle_timeout_seconds=600,
    )

    with pytest.raises(InvalidVerificationTransition):
        manager.submit(
            activated.session_id,
            now=created_at + timedelta(seconds=631),
        )

    assert (
        manager.get(activated.session_id, now=created_at + timedelta(seconds=631)).state
        is VerificationSessionState.EXPIRED
    )


def test_production_compose_flags_connector_and_control() -> None:
    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "deploy/compose.production.yml").read_text()
    )
    connector_environment = compose["services"]["xianyu-connector"]["environment"]
    control_environment = compose["services"]["xianyu-control"]["environment"]

    assert connector_environment["XIANYU_LOCAL_VERIFICATION_HANDOFF_ENABLED"] == (
        "${XIANYU_LOCAL_VERIFICATION_HANDOFF_ENABLED:-false}"
    )
    assert "XIANYU_LOCAL_VERIFICATION_HANDOFF_TTL_SECONDS" not in connector_environment
    assert control_environment["XIANYU_LOCAL_VERIFICATION_HANDOFF_ENABLED"] == (
        "${XIANYU_LOCAL_VERIFICATION_HANDOFF_ENABLED:-false}"
    )
    assert control_environment["XIANYU_LOCAL_VERIFICATION_PUBLIC_ORIGIN"] == (
        "${XIANYU_LOCAL_VERIFICATION_PUBLIC_ORIGIN:-}"
    )


@pytest.mark.asyncio
async def test_local_handoff_succeeds_only_after_all_four_readiness_checks(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, enabled=True)
    _prepare_challenge(settings)
    runtimes = SqliteRuntimeRepository(settings.database_path)
    handoff = LocalVerificationHandoff(
        settings.database_path,
        settings.master_key_path,
        runtimes,
        SqliteCommandRepository(settings.database_path),
        enabled=True,
        recovery_timeout_seconds=1,
        recovery_poll_seconds=0.005,
    )
    created = handoff.create("account-1", "7", "readiness-check")
    handoff.consume("account-1", created["session_id"], created["handoff_token"])
    handoff.complete("account-1", created["session_id"], "7")

    RuntimeService(runtimes).transition_to(
        "account-1",
        AccountState.AUTHENTICATING,
        clear_readiness=True,
    )
    reporter = RuntimeReporter("account-1", runtimes)
    reporter.mark_session(True)
    reporter.mark_token(True)
    reporter.mark_websocket(True)
    await asyncio.sleep(0.02)

    sessions = SqliteVerificationRepository(
        settings.database_path,
        SecretCipher(b"k" * 32),
    )
    assert sessions.get(created["session_id"]).state.value == "verifying"

    reporter.mark_heartbeat()
    await asyncio.sleep(0.02)

    assert sessions.get(created["session_id"]).state.value == "succeeded"
    assert (
        SqliteSecretRepository(
            settings.database_path,
            SecretCipher(b"k" * 32),
        ).get("account-1", "verification_url")
        == ""
    )
    handoff.close()


@pytest.mark.asyncio
async def test_local_handoff_fails_when_platform_returns_a_new_challenge(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, enabled=True)
    _prepare_challenge(settings)
    runtimes = SqliteRuntimeRepository(settings.database_path)
    handoff = LocalVerificationHandoff(
        settings.database_path,
        settings.master_key_path,
        runtimes,
        SqliteCommandRepository(settings.database_path),
        enabled=True,
        recovery_timeout_seconds=1,
        recovery_poll_seconds=0.005,
    )
    created = handoff.create("account-1", "7", "new-challenge-check")
    handoff.consume("account-1", created["session_id"], created["handoff_token"])
    handoff.complete("account-1", created["session_id"], "7")
    SqliteSecretRepository(settings.database_path, SecretCipher(b"k" * 32)).save(
        "account-1",
        "verification_url",
        "https://challenge.goofish.com/verify?action=captcha&x5secdata=new",
    )

    await asyncio.sleep(0.02)

    session = SqliteVerificationRepository(
        settings.database_path,
        SecretCipher(b"k" * 32),
    ).get(created["session_id"])
    assert session.state.value == "failed"
    assert session.reason_code == "new_challenge"
    handoff.close()


@pytest.mark.asyncio
async def test_local_handoff_fails_when_resume_command_fails(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    _prepare_challenge(settings)
    runtimes = SqliteRuntimeRepository(settings.database_path)
    commands = SqliteCommandRepository(settings.database_path)
    handoff = LocalVerificationHandoff(
        settings.database_path,
        settings.master_key_path,
        runtimes,
        commands,
        enabled=True,
        recovery_timeout_seconds=1,
        recovery_poll_seconds=0.005,
    )
    created = handoff.create("account-1", "7", "command-failure-check")
    handoff.consume("account-1", created["session_id"], created["handoff_token"])
    handoff.complete("account-1", created["session_id"], "7")
    command = commands.claim_next()
    assert command is not None
    commands.complete(command.command_id, error_message="worker failed")

    await asyncio.sleep(0.02)

    session = SqliteVerificationRepository(
        settings.database_path,
        SecretCipher(b"k" * 32),
    ).get(created["session_id"])
    assert session.state.value == "failed"
    assert session.reason_code == "recovery_command_failed"
    handoff.close()


def test_local_handoff_rejects_nonpositive_completion_ttl(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)

    with pytest.raises(ValueError, match="completion TTL must be positive"):
        LocalVerificationHandoff(
            settings.database_path,
            settings.master_key_path,
            SqliteRuntimeRepository(settings.database_path),
            SqliteCommandRepository(settings.database_path),
            enabled=True,
            completion_ttl_seconds=0,
        )


def test_local_handoff_requires_pending_manual_verification(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    handoff = LocalVerificationHandoff(
        settings.database_path,
        settings.master_key_path,
        SqliteRuntimeRepository(settings.database_path),
        SqliteCommandRepository(settings.database_path),
        enabled=True,
    )

    with pytest.raises(LocalHandoffConflict, match="no pending manual verification"):
        handoff.create("account-1", "7", "no-manual-challenge")


@pytest.mark.asyncio
async def test_complete_recovers_from_persisted_submit_conflict_without_duplicate_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, enabled=True)
    _prepare_challenge(settings)
    commands = SqliteCommandRepository(settings.database_path)
    handoff = LocalVerificationHandoff(
        settings.database_path,
        settings.master_key_path,
        SqliteRuntimeRepository(settings.database_path),
        commands,
        enabled=True,
        recovery_timeout_seconds=1,
        recovery_poll_seconds=0.01,
    )
    created = handoff.create("account-1", "7", "submit-conflict-retry")
    session_id = created["session_id"]
    handoff.consume("account-1", session_id, created["handoff_token"])
    original_submit = handoff._sessions.submit

    def persist_then_report_conflict(target_session_id: str):
        original_submit(target_session_id)
        raise VerificationVersionConflict("concurrent submit")

    monkeypatch.setattr(handoff._sessions, "submit", persist_then_report_conflict)

    first = handoff.complete("account-1", session_id, "7")
    second = handoff.complete("account-1", session_id, "7")

    assert first["state"] == second["state"] == "submitted"
    assert commands.claim_next() is not None
    assert commands.claim_next() is None
    assert list(handoff._recovery_tasks) == [session_id]
    handoff.close()
    await asyncio.sleep(0)


def test_complete_propagates_submit_conflict_without_persisted_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, enabled=True)
    _prepare_challenge(settings)
    commands = SqliteCommandRepository(settings.database_path)
    handoff = LocalVerificationHandoff(
        settings.database_path,
        settings.master_key_path,
        SqliteRuntimeRepository(settings.database_path),
        commands,
        enabled=True,
    )
    created = handoff.create("account-1", "7", "unpersisted-conflict")
    session_id = created["session_id"]
    handoff.consume("account-1", session_id, created["handoff_token"])

    def reject_submit(_session_id: str) -> None:
        raise VerificationVersionConflict("write lost before persistence")

    monkeypatch.setattr(handoff._sessions, "submit", reject_submit)

    with pytest.raises(VerificationVersionConflict, match="before persistence"):
        handoff.complete("account-1", session_id, "7")

    assert handoff._sessions.get(session_id).state is VerificationSessionState.OPERATOR_ACTIVE
    assert commands.claim_next() is not None
    assert handoff._recovery_tasks == {}


@pytest.mark.asyncio
async def test_terminal_handoff_completion_retires_without_new_recovery_work(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, enabled=True)
    _prepare_challenge(settings)
    commands = SqliteCommandRepository(settings.database_path)
    handoff = LocalVerificationHandoff(
        settings.database_path,
        settings.master_key_path,
        SqliteRuntimeRepository(settings.database_path),
        commands,
        enabled=True,
        recovery_timeout_seconds=1,
        recovery_poll_seconds=0.005,
    )
    created = handoff.create("account-1", "7", "terminal-retirement")
    session_id = created["session_id"]
    handoff.consume("account-1", session_id, created["handoff_token"])
    handoff.complete("account-1", session_id, "7")
    command = commands.claim_next()
    assert command is not None
    commands.complete(command.command_id, error_message="recovery failed")

    for _ in range(20):
        terminal = handoff._sessions.get(session_id)
        if terminal.terminal:
            break
        await asyncio.sleep(0.005)

    repeated = handoff.complete("account-1", session_id, "7")
    assert repeated["state"] == "failed"
    assert repeated["reason_code"] == "recovery_command_failed"
    assert commands.claim_next() is None
    assert handoff._recovery_tasks == {}
    handoff.close()


@pytest.mark.asyncio
async def test_local_handoff_recovery_timeout_finishes_session_and_retires_task(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, enabled=True)
    _prepare_challenge(settings)
    handoff = LocalVerificationHandoff(
        settings.database_path,
        settings.master_key_path,
        SqliteRuntimeRepository(settings.database_path),
        SqliteCommandRepository(settings.database_path),
        enabled=True,
        recovery_timeout_seconds=0,
        recovery_poll_seconds=0,
    )
    created = handoff.create("account-1", "7", "recovery-timeout")
    session_id = created["session_id"]
    handoff.consume("account-1", session_id, created["handoff_token"])
    handoff.complete("account-1", session_id, "7")

    await asyncio.sleep(0)

    session = handoff._sessions.get(session_id)
    assert session.state is VerificationSessionState.FAILED
    assert session.reason_code == "recovery_timeout"
    assert handoff._recovery_tasks == {}
    handoff.close()


@pytest.mark.asyncio
async def test_local_handoff_recovery_stops_when_account_goes_offline(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=True)
    _prepare_challenge(settings)
    runtimes = SqliteRuntimeRepository(settings.database_path)
    handoff = LocalVerificationHandoff(
        settings.database_path,
        settings.master_key_path,
        runtimes,
        SqliteCommandRepository(settings.database_path),
        enabled=True,
        recovery_timeout_seconds=1,
        recovery_poll_seconds=0.005,
    )
    created = handoff.create("account-1", "7", "offline-recovery")
    session_id = created["session_id"]
    handoff.consume("account-1", session_id, created["handoff_token"])
    handoff.complete("account-1", session_id, "7")
    RuntimeService(runtimes).transition_to("account-1", AccountState.PAUSED)

    await asyncio.sleep(0.02)

    session = handoff._sessions.get(session_id)
    assert session.state is VerificationSessionState.FAILED
    assert session.reason_code == "recovery_failed"
    handoff.close()


@pytest.mark.asyncio
async def test_local_handoff_fails_if_account_returns_to_manual_verification(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, enabled=True)
    _prepare_challenge(settings)
    runtimes = SqliteRuntimeRepository(settings.database_path)
    handoff = LocalVerificationHandoff(
        settings.database_path,
        settings.master_key_path,
        runtimes,
        SqliteCommandRepository(settings.database_path),
        enabled=True,
        recovery_timeout_seconds=1,
        recovery_poll_seconds=0.005,
    )
    created = handoff.create("account-1", "7", "manual-return")
    session_id = created["session_id"]
    handoff.consume("account-1", session_id, created["handoff_token"])
    handoff.complete("account-1", session_id, "7")
    service = RuntimeService(runtimes)
    service.transition_to("account-1", AccountState.AUTHENTICATING)
    await asyncio.sleep(0.02)
    service.transition_to("account-1", AccountState.MANUAL_VERIFICATION_REQUIRED)
    await asyncio.sleep(0.02)

    session = handoff._sessions.get(session_id)
    assert session.state is VerificationSessionState.FAILED
    assert session.reason_code == "recovery_failed"
    assert session.reason_message == "平台仍要求人工验证"
    handoff.close()


@pytest.mark.asyncio
async def test_local_handoff_repository_error_is_compensated_to_terminal_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, enabled=True)
    _prepare_challenge(settings)
    runtimes = SqliteRuntimeRepository(settings.database_path)
    handoff = LocalVerificationHandoff(
        settings.database_path,
        settings.master_key_path,
        runtimes,
        SqliteCommandRepository(settings.database_path),
        enabled=True,
        recovery_timeout_seconds=1,
        recovery_poll_seconds=0.005,
    )
    created = handoff.create("account-1", "7", "repository-error")
    session_id = created["session_id"]
    handoff.consume("account-1", session_id, created["handoff_token"])
    handoff.complete("account-1", session_id, "7")
    monkeypatch.setattr(
        runtimes,
        "get",
        lambda _account_id: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    await asyncio.sleep(0.02)

    session = handoff._sessions.get(session_id)
    assert session.state is VerificationSessionState.FAILED
    assert session.reason_code == "recovery_error"
    assert handoff._recovery_tasks == {}
    handoff.close()
