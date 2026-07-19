from __future__ import annotations

import base64
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from xianyu_connector.api import create_connector_app
from xianyu_connector.application.runtime_service import RuntimeService
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository
from xianyu_connector.infrastructure.sqlite_secret_repository import SqliteSecretRepository
from xianyu_connector.infrastructure.verification_process import VerificationProcessSupervisor
from xianyu_connector.security.aes_gcm import SecretCipher
from xianyu_connector.settings import ConnectorSettings


class _FakeVerificationProcess:
    pid = 1234
    session_id = "session-1"

    def is_alive(self) -> bool:
        return True

    def get_frame(self) -> dict[str, object]:
        return {
            "event": "frame",
            "seq": 1,
            "width": 1280,
            "height": 900,
            "mime_type": "image/jpeg",
            "image_base64": "AA==",
        }

    def get_state(self) -> dict[str, object]:
        return {"state": "waiting_for_operator"}

    def get_error(self) -> None:
        return None

    def input(self, action: str, x: float, y: float) -> None:
        del action, x, y

    def complete(self) -> None:
        return None

    def stop(self, **kwargs: object) -> None:
        del kwargs


def _settings(tmp_path: Path) -> ConnectorSettings:
    database_path = tmp_path / "api.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE cookies (id TEXT PRIMARY KEY, value TEXT, user_id INTEGER)")
        connection.execute("CREATE TABLE cookie_status (cookie_id TEXT PRIMARY KEY, enabled INTEGER)")
        apply_connector_schema(connection)
    key_path = tmp_path / "master.key"
    key_path.write_bytes(base64.urlsafe_b64encode(b"k" * 32))
    return ConnectorSettings(
        database_path=database_path,
        profiles_root=tmp_path / "profiles",
        master_key_path=key_path,
        internal_api_token="t" * 32,
        remote_verification_enabled=True,
    )


def test_internal_verification_session_activates_once_with_ticket(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    runtime = SqliteRuntimeRepository(settings.database_path)
    RuntimeService(runtime).transition_to("account-1", AccountState.MANUAL_VERIFICATION_REQUIRED)
    SqliteSecretRepository(
        settings.database_path,
        SecretCipher(b"k" * 32),
    ).save("account-1", "verification_url", "https://challenge.goofish.com/verify")
    fake = _FakeVerificationProcess()

    def fake_start(self, account_id, session_id, challenge_url):
        del challenge_url
        fake.session_id = session_id
        self._processes[account_id] = fake
        return fake

    monkeypatch.setattr(VerificationProcessSupervisor, "start", fake_start)
    app = create_connector_app(settings)
    headers = {"X-Connector-Token": "t" * 32}
    with TestClient(app) as client:
        created = client.post(
            "/internal/accounts/account-1/verification-sessions",
            headers=headers,
            json={"user_id": 7, "idempotency_key": "verify-request-1"},
        )
        assert created.status_code == 200
        ticket = created.json()["access_token"]
        assert "challenge_info" not in created.json()

        activated = client.get(
            "/internal/accounts/account-1/verification-sessions/"
            f"{created.json()['session_id']}",
            headers={**headers, "X-Verification-Ticket": ticket},
        )
        assert activated.status_code == 200
        assert activated.json()["state"] == "operator_active"

        repeated = client.get(
            "/internal/accounts/account-1/verification-sessions/"
            f"{created.json()['session_id']}",
            headers=headers,
        )
        assert repeated.status_code == 200
        assert repeated.json()["state"] == "operator_active"


def test_internal_verification_create_replays_ticket_and_rejects_scope_conflicts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    runtime = SqliteRuntimeRepository(settings.database_path)
    RuntimeService(runtime).transition_to("account-1", AccountState.MANUAL_VERIFICATION_REQUIRED)
    SqliteSecretRepository(
        settings.database_path,
        SecretCipher(b"k" * 32),
    ).save("account-1", "verification_url", "https://challenge.goofish.com/verify")
    fake = _FakeVerificationProcess()
    starts: list[str] = []

    def fake_start(self, account_id, session_id, challenge_url):
        del challenge_url
        starts.append(session_id)
        fake.session_id = session_id
        self._processes[account_id] = fake
        return fake

    monkeypatch.setattr(VerificationProcessSupervisor, "start", fake_start)
    app = create_connector_app(settings)
    headers = {"X-Connector-Token": "t" * 32}
    request = {"user_id": 7, "idempotency_key": "response-loss-attempt"}

    with TestClient(app) as client:
        first = client.post(
            "/internal/accounts/account-1/verification-sessions",
            headers=headers,
            json=request,
        )
        replay = client.post(
            "/internal/accounts/account-1/verification-sessions",
            headers=headers,
            json=request,
        )

        assert first.status_code == 200
        assert replay.status_code == 200
        assert replay.json()["session_id"] == first.json()["session_id"]
        assert replay.json()["access_token"]
        assert replay.json()["access_token"] != first.json()["access_token"]
        assert starts == [first.json()["session_id"]]

        activated = client.get(
            "/internal/accounts/account-1/verification-sessions/"
            f"{replay.json()['session_id']}",
            headers={**headers, "X-Verification-Ticket": replay.json()["access_token"]},
        )
        conflict = client.post(
            "/internal/accounts/account-1/verification-sessions",
            headers=headers,
            json={"user_id": 8, "idempotency_key": "other-attempt"},
        )

        assert activated.status_code == 200
        assert activated.json()["state"] == "operator_active"
        assert conflict.status_code == 409
