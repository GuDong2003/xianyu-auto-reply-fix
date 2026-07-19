from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from xianyu_connector.application.runtime_service import RuntimeService
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository
from xianyu_control.accounts_router import create_accounts_router

ROOT = Path(__file__).resolve().parents[1]


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


def _app(
    database_path: Path,
    calls: list[tuple[str, object]],
    get_handler=None,
    *,
    local_verification_enabled: bool = True,
    local_verification_public_origin: str = "https://testserver",
    handoff_location: str = "https://challenge.goofish.com/verify?secret=hidden",
    handoff_error_status: int | None = None,
    auth_required: bool = False,
) -> FastAPI:
    async def current_user(request: Request) -> dict[str, object]:
        if auth_required and request.headers.get("Authorization") != "Bearer control-token":
            raise HTTPException(status_code=401, detail="not authenticated")
        return {"user_id": 7, "username": "seller", "is_admin": False}

    async def create_verification(
        user: dict[str, object], account_id: str, idempotency_key: str | None = None
    ):
        calls.append(("create", (account_id, idempotency_key)))
        return {
            "session_id": "verify-1",
            "state": "requested",
            "access_token": "one-time-secret",
            "challenge_url": "https://challenge.invalid/?secret=do-not-return",
            "expires_at": "2026-07-18T12:00:00+00:00",
        }

    async def get_verification(
        session_id: str,
        user: dict[str, object],
        account_id: str,
        ticket: str | None = None,
        after_seq: int = 0,
    ):
        calls.append(("get", (session_id, account_id, ticket, after_seq)))
        return {"session_id": session_id, "state": "waiting_for_operator", "seq": 3}

    async def get_frame(
        session_id: str,
        user: dict[str, object],
        account_id: str,
        ticket: str | None = None,
        after_seq: int = 0,
    ):
        calls.append(("frame", (session_id, account_id, ticket, after_seq)))
        return {"session_id": session_id, "seq": 4, "frame": "data:image/png;base64,AA=="}

    async def send_input(
        session_id: str,
        user: dict[str, object],
        account_id: str,
        payload: dict[str, object],
        ticket: str | None = None,
    ):
        calls.append(("input", (session_id, account_id, payload, ticket)))
        return {"accepted": True, "seq": 5}

    async def complete_verification(
        session_id: str,
        user: dict[str, object],
        account_id: str,
        payload: dict[str, object],
        ticket: str | None = None,
    ):
        calls.append(("complete", (session_id, account_id, payload, ticket)))
        return {"session_id": session_id, "state": "verifying"}

    async def cancel_verification(
        session_id: str,
        user: dict[str, object],
        account_id: str,
        ticket: str | None = None,
    ):
        calls.append(("cancel", (session_id, account_id, ticket)))
        return {"session_id": session_id, "state": "cancelled"}

    async def create_local_verification(
        user: dict[str, object],
        account_id: str,
        idempotency_key: str,
    ):
        calls.append(("create-local", (account_id, user["user_id"], idempotency_key)))
        return {
            "session_id": "local-1",
            "state": "waiting_for_operator",
            "handoff_token": "local-grant-secret",
            "challenge_url": handoff_location,
        }

    async def get_local_handoff(session_id: str, account_id: str, ticket: str):
        if handoff_error_status is not None:
            from xianyu_control.connector_client import ConnectorHttpError

            raise ConnectorHttpError(handoff_error_status)
        calls.append(("handoff-local", (session_id, account_id, ticket)))
        return handoff_location

    async def complete_local_verification(
        session_id: str,
        user: dict[str, object],
        account_id: str,
    ):
        calls.append(("complete-local", (session_id, account_id, user["user_id"])))
        return {"session_id": session_id, "state": "verifying"}

    app = FastAPI()
    selected_get_handler = get_handler or get_verification
    app.include_router(
        create_accounts_router(
            database_path,
            current_user,
            lambda *_: None,
            lambda *_: None,
            create_verification=create_verification,
            get_verification=selected_get_handler,
            get_verification_frame=get_frame,
            send_verification_input=send_input,
            complete_verification=complete_verification,
            cancel_verification=cancel_verification,
            create_local_verification=create_local_verification,
            get_local_verification_handoff=get_local_handoff,
            complete_local_verification=complete_local_verification,
            local_verification_enabled=local_verification_enabled,
            local_verification_public_origin=local_verification_public_origin,
        )
    )
    return app


def test_verification_session_uses_httponly_ticket_and_never_returns_it(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, object]] = []
    client = TestClient(_app(_database(tmp_path), calls))

    response = client.post("/api/accounts/account-1/verification-sessions")

    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["session_id"] == "verify-1"
    assert "access_token" not in response.json()
    assert "challenge_url" not in response.json()
    assert "do-not-return" not in response.text
    assert "verification_ticket_verify-1=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert calls[0][0] == "create"


def test_default_verification_idempotency_tracks_runtime_generation(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, object]] = []
    database_path = _database(tmp_path)
    client = TestClient(_app(database_path, calls))

    first = client.post("/api/accounts/account-1/verification-sessions")
    replay = client.post("/api/accounts/account-1/verification-sessions")
    first_key = calls[0][1][1]
    replay_key = calls[1][1][1]

    RuntimeService(SqliteRuntimeRepository(database_path)).transition_to(
        "account-1",
        AccountState.QR_PENDING,
    )
    next_challenge = client.post("/api/accounts/account-1/verification-sessions")
    next_key = calls[2][1][1]
    explicit = client.post(
        "/api/accounts/account-1/verification-sessions",
        headers={"Idempotency-Key": "explicit-attempt-key"},
    )

    assert first.status_code == replay.status_code == next_challenge.status_code == 202
    assert first_key == replay_key
    assert next_key != first_key
    assert explicit.status_code == 202
    assert calls[3][1][1] == "explicit-attempt-key"


def test_verification_frame_input_and_lifecycle_are_account_scoped(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, object]] = []
    client = TestClient(_app(_database(tmp_path), calls))
    created = client.post("/api/accounts/account-1/verification-sessions")
    assert created.status_code == 202

    status = client.get(
        "/api/accounts/account-1/verification-sessions/verify-1",
        params={"after_seq": 2},
    )
    frame = client.get(
        "/api/accounts/account-1/verification-sessions/verify-1/frame",
        params={"after_seq": 3},
    )
    sent = client.post(
        "/api/accounts/account-1/verification-sessions/verify-1/input",
        json={"type": "move", "x": 12, "y": 34},
    )
    completed = client.post(
        "/api/accounts/account-1/verification-sessions/verify-1/complete",
        json={"success": True},
    )
    cancelled = client.delete(
        "/api/accounts/account-1/verification-sessions/verify-1",
    )

    assert status.status_code == 200
    assert status.headers["cache-control"] == "no-store, private"
    assert frame.json()["frame"].startswith("data:image/png")
    assert sent.status_code == 200
    assert completed.status_code == 200
    assert cancelled.status_code == 200
    assert [entry[0] for entry in calls] == [
        "create",
        "get",
        "frame",
        "input",
        "complete",
        "cancel",
    ]
    assert calls[3][1][2] == {
        "action": "move",
        "x": 12,
        "y": 34,
        "button": "left",
    }


def test_verification_session_requires_owned_account(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    client = TestClient(_app(_database(tmp_path), calls))

    response = client.post("/api/accounts/not-owned/verification-sessions")

    assert response.status_code == 404
    assert calls == []


def test_connector_409_is_kept_as_a_safe_control_error(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []

    async def unavailable(*args, **kwargs):
        from xianyu_control.connector_client import ConnectorHttpError

        raise ConnectorHttpError(409)

    client = TestClient(_app(_database(tmp_path), calls, get_handler=unavailable))
    response = client.get(
        "/api/accounts/account-1/verification-sessions/verify-1",
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "当前验证会话不可用，请重新发起人工验证"}


def test_connector_ticket_401_does_not_become_operator_auth_401(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []

    async def invalid_ticket(*args, **kwargs):
        from xianyu_control.connector_client import ConnectorHttpError

        raise ConnectorHttpError(401)

    client = TestClient(
        _app(
            _database(tmp_path),
            calls,
            get_handler=invalid_ticket,
            auth_required=True,
        )
    )
    verification_error = client.get(
        "/api/accounts/account-1/verification-sessions/verify-1",
        headers={"Authorization": "Bearer control-token"},
    )
    bearer_error = client.get(
        "/api/accounts/account-1/verification-sessions/verify-1",
    )

    assert verification_error.status_code == 409
    assert verification_error.json() == {
        "detail": "验证会话票据已失效，请重新发起人工验证"
    }
    assert bearer_error.status_code == 401
    assert bearer_error.json() == {"detail": "not authenticated"}


def test_frontend_has_authenticated_manual_verification_flow() -> None:
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    styles = (ROOT / "static/css/app.css").read_text(encoding="utf-8")

    assert "function isConnectorManualVerificationRequired(runtimeStatus)" in script
    assert "renderConnectorReadiness(cookie.runtime_status)" in script
    assert (
        "account.runtime_status?.connector_state === 'manual_verification_required'" not in script
    )
    assert "function startManualVerification(accountId, options = {})" in script
    assert script.count("startManualVerification(") == 2
    assert "qrSessionId !== qrCodeVerificationState.activeSessionId" in script
    assert "qrSessionId: requestSessionId" in script
    assert "window.RemoteVerificationConsole.start" in script
    assert "function openRemoteVerificationConsole(accountId, options = {})" in script
    assert 'data-action="remote-verification"' in script
    assert 'onclick="openRemoteVerificationConsole(' in script
    assert "LocalVerificationHandoff" not in script
    assert "openLocalVerificationHandoff" not in script
    assert 'data-action="face-verification"' not in script
    assert "function showFaceVerification(" not in script
    assert "showAccountFaceVerificationModal" not in script
    assert "/face-verification/screenshot/" not in script
    assert "manualVerificationState" not in script
    assert "manualVerificationFetch" not in script
    assert "pendingFrame" not in script
    assert "inputQueue" not in script
    assert 'data-action="qr-relogin"' in script
    assert 'data-action="manual-verification"' not in script
    assert 'data-action="manual-verification-continue"' not in script
    assert "打开实时验证" in script
    assert ".account-action-btn.account-action-qr-relogin .action-text" in styles
    assert "display: inline;" in styles
    assert "verification_ticket_" not in script


def test_local_handoff_create_only_returns_same_origin_url_and_sets_grant_cookie(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, object]] = []
    client = TestClient(_app(_database(tmp_path), calls), base_url="https://testserver")

    response = client.post(
        "/api/accounts/account-1/local-verification-sessions",
        headers={"Idempotency-Key": "local-create-attempt-1"},
    )

    assert response.status_code == 201
    assert response.json() == {
        "handoff_url": "/api/accounts/account-1/local-verification-sessions/local-1/handoff"
    }
    assert "local-grant-secret" not in response.text
    assert "challenge.goofish.com" not in response.text
    cookie = response.headers["set-cookie"]
    assert "local_verification_grant_local-1=local-grant-secret" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    assert "Max-Age=60" in cookie
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert calls[0][0] == "create-local"
    assert calls[0][1][0:2] == ("account-1", 7)
    assert calls[0][1][2] == "local-create-attempt-1"


def test_local_handoff_navigation_needs_no_bearer_and_consumes_grant_cookie(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, object]] = []
    client = TestClient(
        _app(_database(tmp_path), calls, auth_required=True),
        base_url="https://testserver",
    )
    created = client.post(
        "/api/accounts/account-1/local-verification-sessions",
        headers={
            "Authorization": "Bearer control-token",
            "Idempotency-Key": "local-create-attempt-1",
        },
    )

    response = client.get(created.json()["handoff_url"], follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://challenge.goofish.com/verify?secret=hidden"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert calls[-1] == (
        "handoff-local",
        ("local-1", "account-1", "local-grant-secret"),
    )

    replay = client.get(created.json()["handoff_url"], follow_redirects=False)
    assert replay.status_code == 410


def test_local_handoff_rejects_cross_origin_navigation_headers(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    client = TestClient(_app(_database(tmp_path), calls), base_url="https://testserver")
    created = client.post(
        "/api/accounts/account-1/local-verification-sessions",
        headers={"Idempotency-Key": "local-create-attempt-1"},
    )
    path = created.json()["handoff_url"]

    wrong_host = client.get(path, headers={"Host": "evil.example"}, follow_redirects=False)
    wrong_origin = client.get(
        path,
        headers={"Origin": "https://evil.example"},
        follow_redirects=False,
    )
    cross_site = client.get(
        path,
        headers={"Sec-Fetch-Site": "cross-site"},
        follow_redirects=False,
    )
    wrong_mode = client.get(
        path,
        headers={"Sec-Fetch-Mode": "cors"},
        follow_redirects=False,
    )
    wrong_destination = client.get(
        path,
        headers={"Sec-Fetch-Dest": "empty"},
        follow_redirects=False,
    )

    assert wrong_host.status_code == 403
    assert wrong_origin.status_code == 403
    assert cross_site.status_code == 403
    assert wrong_mode.status_code == 403
    assert wrong_destination.status_code == 403
    assert not any(call[0] == "handoff-local" for call in calls)

    valid_navigation = client.get(
        path,
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        },
        follow_redirects=False,
    )
    assert valid_navigation.status_code == 302


def test_local_handoff_blocks_unsafe_connector_redirect(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    client = TestClient(
        _app(_database(tmp_path), calls, handoff_location="https://evil.example/steal"),
        base_url="https://testserver",
    )
    created = client.post(
        "/api/accounts/account-1/local-verification-sessions",
        headers={"Idempotency-Key": "local-create-attempt-1"},
    )

    response = client.get(created.json()["handoff_url"], follow_redirects=False)

    assert response.status_code == 502
    assert "evil.example" not in response.text


def test_expired_local_handoff_deletes_grant_cookie(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    client = TestClient(
        _app(_database(tmp_path), calls, handoff_error_status=410),
        base_url="https://testserver",
    )
    created = client.post(
        "/api/accounts/account-1/local-verification-sessions",
        headers={"Idempotency-Key": "local-create-attempt-1"},
    )

    response = client.get(created.json()["handoff_url"], follow_redirects=False)

    assert response.status_code == 410
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store, private"


def test_local_complete_uses_authenticated_operator_and_rejects_client_payload(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, object]] = []
    client = TestClient(_app(_database(tmp_path), calls), base_url="https://testserver")
    complete_url = "/api/accounts/account-1/local-verification-sessions/local-1/complete"

    response = client.post(complete_url, headers={"Idempotency-Key": "local-complete-1"})
    rejected = client.post(
        complete_url,
        headers={"Idempotency-Key": "local-complete-2"},
        json={"success": True},
    )

    assert response.status_code == 202
    assert response.json() == {"session_id": "local-1", "state": "verifying"}
    assert calls[-1] == ("complete-local", ("local-1", "account-1", 7))
    assert rejected.status_code == 422


def test_local_complete_requires_control_authentication(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    client = TestClient(
        _app(_database(tmp_path), calls, auth_required=True),
        base_url="https://testserver",
    )
    complete_url = "/api/accounts/account-1/local-verification-sessions/local-1/complete"

    anonymous = client.post(complete_url, headers={"Idempotency-Key": "local-complete-1"})
    authenticated = client.post(
        complete_url,
        headers={
            "Authorization": "Bearer control-token",
            "Idempotency-Key": "local-complete-1",
        },
    )

    assert anonymous.status_code == 401
    assert authenticated.status_code == 202
    assert calls[-1] == ("complete-local", ("local-1", "account-1", 7))


def test_local_handoff_is_disabled_by_default(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    client = TestClient(
        _app(_database(tmp_path), calls, local_verification_enabled=False),
        base_url="https://testserver",
    )

    response = client.post(
        "/api/accounts/account-1/local-verification-sessions",
        headers={"Idempotency-Key": "local-create-attempt-1"},
    )

    assert response.status_code == 503
    assert calls == []


def test_local_handoff_requires_public_origin_when_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("XIANYU_LOCAL_VERIFICATION_PUBLIC_ORIGIN", raising=False)
    calls: list[tuple[str, object]] = []
    client = TestClient(
        _app(
            _database(tmp_path),
            calls,
            local_verification_enabled=True,
            local_verification_public_origin="",
        ),
        base_url="https://testserver",
    )

    response = client.post(
        "/api/accounts/account-1/local-verification-sessions",
        headers={"Idempotency-Key": "local-create-attempt-1"},
    )

    assert response.status_code == 503
    assert calls == []


def test_local_handoff_create_requires_idempotency_key(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    client = TestClient(_app(_database(tmp_path), calls), base_url="https://testserver")

    response = client.post("/api/accounts/account-1/local-verification-sessions")

    assert response.status_code == 422
    assert calls == []


def test_local_handoff_retry_reuses_key_session_and_resets_cookie(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    client = TestClient(_app(_database(tmp_path), calls), base_url="https://testserver")
    headers = {"Idempotency-Key": "response-loss-attempt"}

    first = client.post(
        "/api/accounts/account-1/local-verification-sessions",
        headers=headers,
    )
    retry = client.post(
        "/api/accounts/account-1/local-verification-sessions",
        headers=headers,
    )

    assert first.status_code == retry.status_code == 201
    assert first.json() == retry.json()
    assert first.headers["set-cookie"] == retry.headers["set-cookie"]
    assert [call[1][2] for call in calls] == [
        "response-loss-attempt",
        "response-loss-attempt",
    ]


def test_each_local_handoff_attempt_forwards_its_idempotency_key(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    client = TestClient(_app(_database(tmp_path), calls), base_url="https://testserver")

    client.post(
        "/api/accounts/account-1/local-verification-sessions",
        headers={"Idempotency-Key": "local-create-attempt-1"},
    )
    client.post(
        "/api/accounts/account-1/local-verification-sessions",
        headers={"Idempotency-Key": "local-create-attempt-2"},
    )

    keys = [call[1][2] for call in calls if call[0] == "create-local"]
    assert keys == ["local-create-attempt-1", "local-create-attempt-2"]
