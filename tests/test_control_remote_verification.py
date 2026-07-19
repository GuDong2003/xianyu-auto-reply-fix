from __future__ import annotations

import asyncio
import os
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from starlette.websockets import WebSocketDisconnect

from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_control.accounts_router import create_accounts_router
from xianyu_control.remote_verification_proof import (
    OperatorProofSigner,
    ProofStatus,
    ProofVerification,
)


def _database(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "control.db"
    with sqlite3.connect(path) as connection:
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
    return path


class FakeRemoteSocket:
    def __init__(self) -> None:
        self.sent: list[str | bytes] = []
        self._incoming: asyncio.Queue[str | bytes] = asyncio.Queue()
        self._incoming.put_nowait(b"rfb-frame")

    async def recv(self) -> str | bytes:
        return await self._incoming.get()

    async def send(self, data: str | bytes) -> None:
        self.sent.append(data)
        await self._incoming.put(b"rfb-ack")

    def queue_frame(self, data: str | bytes) -> None:
        self._incoming.put_nowait(data)


class WebSocketHeaderRecorder:
    def __init__(self, app: FastAPI, records: list[dict[str, str]]) -> None:
        self._app = app
        self._records = records

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "websocket":
            self._records.append(
                {
                    name.decode("latin-1").lower(): value.decode("latin-1")
                    for name, value in scope.get("headers", ())
                }
            )
        await self._app(scope, receive, send)


def _write_test_certificate(root: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "testserver")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("testserver")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    certificate_path = root / "testserver-cert.pem"
    key_path = root / "testserver-key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return certificate_path, key_path


@contextmanager
def _serve_https(app, root: Path) -> Iterator[int]:
    certificate_path, key_path = _write_test_certificate(root)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="error",
            access_log=False,
            lifespan="off",
            ssl_certfile=str(certificate_path),
            ssl_keyfile=str(key_path),
        )
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("test HTTPS server did not start")
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError("test HTTPS server did not stop")


def _chromium_executable(playwright_executable: str) -> Path:
    candidates = (
        os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", ""),
        playwright_executable,
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
    )
    executable = next(
        (Path(candidate) for candidate in candidates if candidate and os.access(candidate, os.X_OK)),
        None,
    )
    if executable is None:
        pytest.fail("a Chromium executable is required for the WebSocket handshake contract")
    return executable


def _app(
    database_path: Path,
    bridge_calls: list[tuple[str, str, str, str]],
    remote_socket: FakeRemoteSocket,
    *,
    auth_required: bool = True,
    remote_enabled: bool | None = True,
    public_origin: str | None = "https://testserver",
    session_state: dict[str, str] | None = None,
) -> FastAPI:
    async def current_user(request: Request) -> dict[str, object]:
        if auth_required and request.headers.get("Authorization") != "Bearer control-token":
            raise HTTPException(status_code=401, detail="not authenticated")
        return {"user_id": 7, "username": "seller", "is_admin": False}

    async def create_verification(user, account_id, idempotency_key):
        return {
            "session_id": "verify-1",
            "account_id": account_id,
            "state": "waiting_for_operator",
            "access_token": "connector-ticket",
        }

    async def get_verification(session_id, user, account_id, **kwargs):
        return {
            "session_id": session_id,
            "account_id": account_id,
            "state": (session_state or {}).get(session_id, "operator_active"),
        }

    async def complete_verification(session_id, user, account_id, payload, **kwargs):
        return {"session_id": session_id, "account_id": account_id, "state": "verifying"}

    @asynccontextmanager
    async def connect_remote(
        account_id: str,
        session_id: str,
        *,
        ticket: str,
        operator_id: str,
    ):
        bridge_calls.append((account_id, session_id, ticket, operator_id))
        yield remote_socket

    app = FastAPI()
    app.include_router(
        create_accounts_router(
            database_path,
            current_user,
            lambda *_: None,
            lambda *_: None,
            create_verification=create_verification,
            get_verification=get_verification,
            complete_verification=complete_verification,
            connect_remote_verification=connect_remote,
            remote_verification_enabled=remote_enabled,
            remote_verification_public_origin=public_origin,
            remote_verification_proof_secret="p" * 32,
        )
    )
    return app


def _mint(client: TestClient) -> tuple[str, str]:
    created = client.post(
        "/api/accounts/account-1/verification-sessions",
        headers={
            "Authorization": "Bearer control-token",
            "Idempotency-Key": "remote-create-attempt-1",
        },
    )
    assert created.status_code == 202
    minted = client.post(
        "/api/accounts/account-1/verification-sessions/verify-1/remote-proof",
        headers={"Authorization": "Bearer control-token"},
    )
    assert minted.status_code == 201
    return minted.json()["viewer_url"], minted.json()["websocket_url"]


def _viewer_headers(**overrides: str) -> dict[str, str]:
    headers = {
        "Host": "testserver",
        "Origin": "https://testserver",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "iframe",
    }
    headers.update(overrides)
    return headers


def _websocket_headers(**overrides: str) -> dict[str, str]:
    headers = {
        "Host": "testserver",
        "Origin": "https://testserver",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "websocket",
        "Sec-Fetch-Dest": "empty",
    }
    headers.update(overrides)
    return headers


def _cookie_header(client: TestClient) -> str:
    return "; ".join(f"{name}={value}" for name, value in client.cookies.items())


def _assert_viewer_security_headers(response, *, websocket_allowed: bool = False) -> None:
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'self'" in csp
    if websocket_allowed:
        assert "connect-src 'self' wss://testserver" in csp
    else:
        assert "connect-src 'none'" in csp


def test_operator_proof_binds_operator_account_session_and_expiry() -> None:
    signer = OperatorProofSigner("p" * 32, ttl_seconds=2)
    token = signer.issue(7, "account-1", "verify-1", now=100)

    valid = signer.verify(token, 7, "account-1", "verify-1", now=101)
    wrong_user = signer.verify(token, 8, "account-1", "verify-1", now=101)
    wrong_account = signer.verify(token, 7, "account-2", "verify-1", now=101)
    wrong_session = signer.verify(token, 7, "account-1", "verify-2", now=101)
    expired = signer.verify(token, 7, "account-1", "verify-1", now=103)

    assert valid.status is ProofStatus.VALID
    assert wrong_user.status is ProofStatus.INVALID
    assert wrong_account.status is ProofStatus.INVALID
    assert wrong_session.status is ProofStatus.INVALID
    assert expired.status is ProofStatus.EXPIRED


def test_bearer_post_mints_only_same_origin_paths_and_secure_cookie(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str, str]] = []
    client = TestClient(_app(_database(tmp_path), calls, FakeRemoteSocket()), base_url="https://testserver")

    viewer_url, websocket_url = _mint(client)

    assert viewer_url == "/api/accounts/account-1/verification-sessions/verify-1/viewer"
    assert websocket_url == "/api/accounts/account-1/verification-sessions/verify-1/remote"
    response = client.post(
        "/api/accounts/account-1/verification-sessions/verify-1/remote-proof",
        headers={"Authorization": "Bearer control-token"},
    )
    cookie = response.headers["set-cookie"]
    assert "verification_remote_proof_verify-1=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    assert "Max-Age=300" in cookie
    assert "proof" not in response.text.lower()
    assert "connector-ticket" not in response.text

    navigation_without_origin = _viewer_headers()
    navigation_without_origin.pop("Origin")
    assert client.get(viewer_url, headers=navigation_without_origin).status_code == 200


def test_remote_proof_requires_bearer_and_available_configuration(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str, str]] = []
    socket = FakeRemoteSocket()
    client = TestClient(_app(_database(tmp_path), calls, socket), base_url="https://testserver")
    client.post(
        "/api/accounts/account-1/verification-sessions",
        headers={
            "Authorization": "Bearer control-token",
            "Idempotency-Key": "remote-create-attempt-1",
        },
    )

    anonymous = client.post(
        "/api/accounts/account-1/verification-sessions/verify-1/remote-proof"
    )
    unavailable = TestClient(
        _app(_database(tmp_path / "disabled"), calls, socket, remote_enabled=False),
        base_url="https://testserver",
    ).post(
        "/api/accounts/account-1/verification-sessions/verify-1/remote-proof",
        headers={"Authorization": "Bearer control-token"},
    )

    assert anonymous.status_code == 401
    assert unavailable.status_code == 503


def test_remote_configuration_does_not_fallback_to_local_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XIANYU_LOCAL_VERIFICATION_HANDOFF_ENABLED", "true")
    monkeypatch.setenv("XIANYU_LOCAL_VERIFICATION_PUBLIC_ORIGIN", "https://testserver")
    monkeypatch.setenv("XIANYU_REMOTE_VERIFICATION_ENABLED", "true")
    monkeypatch.setenv("XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN", "https://testserver")
    calls: list[tuple[str, str, str, str]] = []
    client = TestClient(
        _app(_database(tmp_path / "enabled"), calls, FakeRemoteSocket(), remote_enabled=None, public_origin=None),
        base_url="https://testserver",
    )
    client.post(
        "/api/accounts/account-1/verification-sessions",
        headers={"Authorization": "Bearer control-token", "Idempotency-Key": "remote-create-attempt-1"},
    )
    assert client.post(
        "/api/accounts/account-1/verification-sessions/verify-1/remote-proof",
        headers={"Authorization": "Bearer control-token"},
    ).status_code == 201

    monkeypatch.setenv("XIANYU_REMOTE_VERIFICATION_ENABLED", "false")
    disabled = TestClient(
        _app(_database(tmp_path / "disabled"), calls, FakeRemoteSocket(), remote_enabled=None, public_origin=None),
        base_url="https://testserver",
    )
    disabled.post(
        "/api/accounts/account-1/verification-sessions",
        headers={"Authorization": "Bearer control-token", "Idempotency-Key": "remote-create-attempt-1"},
    )
    assert disabled.post(
        "/api/accounts/account-1/verification-sessions/verify-1/remote-proof",
        headers={"Authorization": "Bearer control-token"},
    ).status_code == 503


def test_viewer_is_same_origin_csp_locked_and_never_contains_proof(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str, str]] = []
    client = TestClient(_app(_database(tmp_path), calls, FakeRemoteSocket()), base_url="https://testserver")
    viewer_url, _ = _mint(client)

    response = client.get(viewer_url, headers=_viewer_headers())

    assert response.status_code == 200
    _assert_viewer_security_headers(response, websocket_allowed=True)
    assert "default-src 'none'" in response.headers["content-security-policy"]
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in csp
    assert "connect-src 'self' wss://testserver" in csp
    assert "connect-src wss:" not in csp
    assert "connect-src 'self' wss:;" not in csp
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["cache-control"] == "no-store, private"
    assert "remote-verification-viewer.js" in response.text
    assert "verification_remote_proof" not in response.text
    assert "connector-ticket" not in response.text


def test_expired_viewer_proof_returns_410_and_clears_cookie(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(
        _app(_database(tmp_path), [], FakeRemoteSocket()),
        base_url="https://testserver",
    )
    viewer_url, _ = _mint(client)
    monkeypatch.setattr(
        OperatorProofSigner,
        "verify_bound",
        lambda *_args, **_kwargs: ProofVerification(ProofStatus.EXPIRED, 1, "7"),
    )

    response = client.get(viewer_url, headers=_viewer_headers())

    assert response.status_code == 410
    _assert_viewer_security_headers(response)
    assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.parametrize(
    "headers",
    [
        _viewer_headers(Host="evil.example"),
        _viewer_headers(Origin="https://evil.example"),
        _viewer_headers(**{"Sec-Fetch-Site": "cross-site"}),
        _viewer_headers(**{"Sec-Fetch-Mode": "cors"}),
        _viewer_headers(**{"Sec-Fetch-Dest": "document"}),
    ],
)
def test_viewer_rejects_wrong_host_origin_or_fetch_metadata(
    tmp_path: Path,
    headers: dict[str, str],
) -> None:
    client = TestClient(
        _app(_database(tmp_path), [], FakeRemoteSocket()),
        base_url="https://testserver",
    )
    viewer_url, _ = _mint(client)

    response = client.get(viewer_url, headers=headers)
    assert response.status_code == 403
    _assert_viewer_security_headers(response)


def test_remote_websocket_uses_cookie_auth_and_bridges_binary_frames(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str, str]] = []
    remote_socket = FakeRemoteSocket()
    client = TestClient(_app(_database(tmp_path), calls, remote_socket), base_url="https://testserver")
    _, websocket_url = _mint(client)

    headers = _websocket_headers(Cookie=_cookie_header(client))
    with client.websocket_connect(websocket_url, headers=headers) as websocket:
        assert websocket.receive_bytes() == b"rfb-frame"
        websocket.send_bytes(b"pointer-event")
        assert websocket.receive_bytes() == b"rfb-ack"

    assert calls == [("account-1", "verify-1", "connector-ticket", "7")]
    assert remote_socket.sent == [b"pointer-event"]


def test_real_chromium_websocket_accepts_valid_cookies_without_fetch_metadata(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, str, str]] = []
    handshakes: list[dict[str, str]] = []
    app = _app(_database(tmp_path), calls, FakeRemoteSocket())

    with (
        _serve_https(WebSocketHeaderRecorder(app, handshakes), tmp_path) as port,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=str(_chromium_executable(playwright.chromium.executable_path)),
            args=[
                "--ignore-certificate-errors",
                "--allow-insecure-localhost",
                f"--host-resolver-rules=MAP testserver:443 127.0.0.1:{port}",
            ],
        )
        page = browser.new_page()
        page.goto("https://testserver/", wait_until="domcontentloaded")
        result = page.evaluate(
            """async () => {
                  const commonHeaders = {Authorization: 'Bearer control-token'};
                  const created = await fetch('/api/accounts/account-1/verification-sessions', {
                    method: 'POST',
                    headers: {...commonHeaders, 'Idempotency-Key': 'chromium-handshake-1'},
                  });
                  const minted = await fetch(
                    '/api/accounts/account-1/verification-sessions/verify-1/remote-proof',
                    {method: 'POST', headers: commonHeaders},
                  );
                  if (created.status !== 202 || minted.status !== 201) {
                    return {state: 'setup-failed', created: created.status, minted: minted.status};
                  }
                  return await new Promise((resolve) => {
                    const socket = new WebSocket(
                      'wss://testserver/api/accounts/account-1/verification-sessions/verify-1/remote',
                    );
                    socket.binaryType = 'arraybuffer';
                    const timeout = setTimeout(() => {
                      socket.close();
                      resolve({state: 'timeout'});
                    }, 5000);
                    socket.onmessage = (event) => {
                      clearTimeout(timeout);
                      const text = new TextDecoder().decode(new Uint8Array(event.data));
                      socket.close();
                      resolve({state: 'message', text});
                    };
                    socket.onclose = (event) => {
                      clearTimeout(timeout);
                      resolve({state: 'closed', code: event.code});
                    };
                  });
                }"""
        )
        browser.close()

    assert result == {"state": "message", "text": "rfb-frame"}
    assert calls == [("account-1", "verify-1", "connector-ticket", "7")]
    assert len(handshakes) == 1
    handshake = handshakes[0]
    assert handshake["host"] == "testserver"
    assert handshake["origin"] == "https://testserver"
    assert {
        part.split("=", 1)[0].strip() for part in handshake["cookie"].split(";")
    } == {
        "verification_ticket_verify-1",
        "verification_remote_proof_verify-1",
    }
    assert "sec-fetch-site" not in handshake
    assert "sec-fetch-mode" not in handshake
    assert "sec-fetch-dest" not in handshake


def test_remote_proof_is_a_short_reconnect_lease_and_can_be_revoked(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str, str]] = []
    remote_socket = FakeRemoteSocket()
    client = TestClient(
        _app(_database(tmp_path), calls, remote_socket),
        base_url="https://testserver",
    )
    viewer_url, websocket_url = _mint(client)
    headers = _websocket_headers(Cookie=_cookie_header(client))

    with client.websocket_connect(websocket_url, headers=headers) as websocket:
        assert websocket.receive_bytes() == b"rfb-frame"
    remote_socket.queue_frame(b"rfb-reconnect")
    with client.websocket_connect(websocket_url, headers=headers) as websocket:
        assert websocket.receive_bytes() == b"rfb-reconnect"

    revoked = client.delete(
        "/api/accounts/account-1/verification-sessions/verify-1/remote-proof",
        headers={"Authorization": "Bearer control-token"},
    )
    assert revoked.status_code == 204
    assert "verification_remote_proof_verify-1=" in revoked.headers["set-cookie"]
    assert "Max-Age=0" in revoked.headers["set-cookie"]
    response = client.get(viewer_url, headers=_viewer_headers())
    assert response.status_code == 403
    _assert_viewer_security_headers(response)


def test_unavailable_viewer_response_is_not_cached(tmp_path: Path) -> None:
    client = TestClient(
        _app(_database(tmp_path), [], FakeRemoteSocket(), remote_enabled=False),
        base_url="https://testserver",
    )

    response = client.get(
        "/api/accounts/account-1/verification-sessions/verify-1/viewer",
        headers=_viewer_headers(),
    )

    assert response.status_code == 503
    _assert_viewer_security_headers(response)


def test_active_remote_websocket_makes_second_viewer_occupied(tmp_path: Path) -> None:
    client = TestClient(
        _app(_database(tmp_path), [], FakeRemoteSocket()),
        base_url="https://testserver",
    )
    viewer_url, websocket_url = _mint(client)
    headers = _websocket_headers(Cookie=_cookie_header(client))

    with client.websocket_connect(websocket_url, headers=headers) as websocket:
        assert websocket.receive_bytes() == b"rfb-frame"
        occupied = client.get(viewer_url, headers=_viewer_headers())
        assert occupied.status_code == 409
        _assert_viewer_security_headers(occupied)
        remint = client.post(
            "/api/accounts/account-1/verification-sessions/verify-1/remote-proof",
            headers={"Authorization": "Bearer control-token"},
        )
        assert remint.status_code == 409


@pytest.mark.parametrize(
    ("path_suffix", "headers"),
    [
        ("?token=browser-secret", _websocket_headers()),
        ("", _websocket_headers(Host="evil.example")),
        ("", _websocket_headers(Origin="https://evil.example")),
        ("", _websocket_headers(**{"Sec-Fetch-Site": "cross-site"})),
        ("", _websocket_headers(**{"Sec-Fetch-Mode": "cors"})),
        ("", _websocket_headers(**{"Sec-Fetch-Dest": "document"})),
    ],
)
def test_remote_websocket_rejects_query_credentials_and_cross_origin(
    tmp_path: Path,
    path_suffix: str,
    headers: dict[str, str],
) -> None:
    client = TestClient(
        _app(_database(tmp_path), [], FakeRemoteSocket()),
        base_url="https://testserver",
    )
    _, websocket_url = _mint(client)

    with pytest.raises(WebSocketDisconnect) as error:
        headers["Cookie"] = _cookie_header(client)
        with client.websocket_connect(websocket_url + path_suffix, headers=headers):
            pass

    assert error.value.code == 1008


@pytest.mark.parametrize(
    "missing_cookie",
    [
        "verification_ticket_verify-1",
        "verification_remote_proof_verify-1",
    ],
)
def test_remote_websocket_rejects_missing_viewer_or_operator_cookie(
    tmp_path: Path,
    missing_cookie: str,
) -> None:
    calls: list[tuple[str, str, str, str]] = []
    client = TestClient(
        _app(_database(tmp_path), calls, FakeRemoteSocket()),
        base_url="https://testserver",
    )
    _, websocket_url = _mint(client)
    cookies = dict(client.cookies.items())
    cookies.pop(missing_cookie)
    client.cookies.clear()
    cookie_header = "; ".join(f"{name}={value}" for name, value in cookies.items())

    with pytest.raises(WebSocketDisconnect) as error, client.websocket_connect(
        websocket_url,
        headers=_websocket_headers(Cookie=cookie_header),
    ):
        pass

    assert error.value.code == 1008
    assert calls == []


def test_remote_websocket_rechecks_session_state_before_connecting(tmp_path: Path) -> None:
    state = {"verify-1": "operator_active"}
    calls: list[tuple[str, str, str, str]] = []
    client = TestClient(
        _app(_database(tmp_path), calls, FakeRemoteSocket(), session_state=state),
        base_url="https://testserver",
    )
    _, websocket_url = _mint(client)
    state["verify-1"] = "succeeded"
    headers = _websocket_headers(Cookie=_cookie_header(client))

    with pytest.raises(WebSocketDisconnect) as error, client.websocket_connect(
        websocket_url,
        headers=headers,
    ):
        pass

    assert error.value.code == 1008
    assert calls == []
