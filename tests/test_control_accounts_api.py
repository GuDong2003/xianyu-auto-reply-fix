from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xianyu_connector.application.runtime_reporter import RuntimeReporter
from xianyu_connector.application.runtime_service import RuntimeService
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository
from xianyu_control.accounts_router import create_accounts_router


def _app(database_path: Path, calls: list[tuple[str, str]]) -> FastAPI:
    async def current_user():
        return {"user_id": 7, "username": "seller", "is_admin": False}

    async def generate_qr(user, account_id):
        calls.append(("create", account_id))
        return {
            "success": True,
            "session_id": "qr-1",
            "qr_code_url": "data:image/png;base64,AA==",
        }

    async def check_qr(session_id, user, account_id):
        calls.append(("check", account_id))
        return {"status": "waiting", "session_id": session_id}

    app = FastAPI()
    app.include_router(create_accounts_router(database_path, current_user, generate_qr, check_qr))
    return app


def _database(tmp_path: Path) -> Path:
    database_path = tmp_path / "control.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL);
            CREATE TABLE cookies (
                id TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                user_id INTEGER NOT NULL
            );
            INSERT INTO users (id, username) VALUES (7, 'seller');
            INSERT INTO cookies (id, value, user_id) VALUES ('account-1', 'encrypted', 7);
            """
        )
        apply_connector_schema(connection)
    return database_path


def test_runtime_and_command_endpoints_are_account_scoped_and_idempotent(
    tmp_path: Path,
) -> None:
    database_path = _database(tmp_path)
    client = TestClient(_app(database_path, []))

    runtime = client.get("/api/accounts/account-1/runtime")
    first = client.post(
        "/api/accounts/account-1/commands",
        headers={"Idempotency-Key": "same-command"},
        json={"command": "start"},
    )
    second = client.post(
        "/api/accounts/account-1/commands",
        headers={"Idempotency-Key": "same-command"},
        json={"command": "start"},
    )

    assert runtime.status_code == 200
    assert runtime.json()["readiness"]["online"] is False
    assert first.status_code == 202
    assert first.json()["command_id"] == second.json()["command_id"]
    assert client.get("/api/accounts/missing/runtime").status_code == 404


def test_targeted_qr_routes_keep_account_context(tmp_path: Path) -> None:
    database_path = _database(tmp_path)
    calls: list[tuple[str, str]] = []
    client = TestClient(_app(database_path, calls))

    created = client.post("/api/accounts/account-1/qr-sessions")
    checked = client.get("/api/accounts/account-1/qr-sessions/qr-1")

    assert created.status_code == 202
    assert checked.status_code == 200
    assert calls == [("create", "account-1"), ("check", "account-1")]


def test_direct_relogin_qr_command_requires_qr_session_route(tmp_path: Path) -> None:
    database_path = _database(tmp_path)
    client = TestClient(_app(database_path, []))

    response = client.post(
        "/api/accounts/account-1/commands",
        headers={"Idempotency-Key": "direct-relogin"},
        json={"command": "relogin_qr"},
    )

    assert response.status_code == 409
    assert "/qr-sessions" in response.json()["detail"]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM account_commands").fetchone()[0] == 0


def test_ready_requires_all_four_online_signals(tmp_path: Path) -> None:
    database_path = _database(tmp_path)
    repository = SqliteRuntimeRepository(database_path)
    RuntimeService(repository).transition_to("account-1", AccountState.AUTHENTICATING)
    reporter = RuntimeReporter("account-1", repository)
    reporter.mark_session(True)
    reporter.mark_token(True)
    reporter.mark_websocket(True)
    client = TestClient(_app(database_path, []))

    assert client.get("/health/ready").status_code == 503

    reporter.mark_heartbeat()

    assert client.get("/health/ready").status_code == 200
