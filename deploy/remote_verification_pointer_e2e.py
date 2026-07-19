#!/usr/bin/env python3
"""Exercise pointer input through the production control and connector RFB path."""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from xianyu_connector.api import create_connector_app
from xianyu_connector.application.runtime_service import RuntimeService
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository
from xianyu_connector.infrastructure.sqlite_secret_repository import SqliteSecretRepository
from xianyu_connector.security.aes_gcm import SecretCipher
from xianyu_connector.settings import ConnectorSettings
from xianyu_control.accounts_router import create_accounts_router
from xianyu_control.browser_runtime import resolve_production_chromium_executable
from xianyu_control.connector_client import ConnectorClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_ID = "account-1"
CONTROL_HOST = "control.test"
CHALLENGE_HOST = "challenge.goofish.com"
SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 900
MIN_HORIZONTAL_DRAG_DELTA = 100
RUNNER_PATH = PROJECT_ROOT / "deploy" / "run-remote-verification-pointer-e2e.sh"

_CHALLENGE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
html,body,#target{width:100%;height:100%;margin:0;overflow:hidden;background:#4aa564}
</style></head><body><main id="target"></main><script>
for (const type of ['pointerdown', 'pointermove', 'pointerup']) {
  document.addEventListener(type, event => {
    fetch('/events', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({type:event.type,x:event.clientX,y:event.clientY,buttons:event.buttons}),
      keepalive: true,
    }).catch(() => {});
  }, true);
}
</script></body></html>"""

_DRIVER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
html,body,#viewer{width:100%;height:100%;margin:0;border:0;overflow:hidden;background:#111}
</style></head><body><iframe id="viewer" title="remote verification"></iframe><script>
const controlToken = __CONTROL_TOKEN_JSON__;
window.e2e = {state: 'starting', timeline: [{state: 'starting'}]};
const transition = (state, details = {}) => {
  window.e2e.state = state;
  Object.assign(window.e2e, details);
  window.e2e.timeline.push({state, ...details});
};
window.addEventListener('message', event => {
  if (event.origin !== window.location.origin || event.data?.type !== 'remote-verification-viewer') return;
  transition(event.data.state, {viewerMessage: event.data});
});
window.addEventListener('error', event => transition('failed', {error: String(event.message)}));
window.addEventListener('unhandledrejection', event => {
  transition('failed', {error: String(event.reason?.message || event.reason)});
});
window.e2eProbeSession = async () => {
  if (!window.e2e.sessionId) return null;
  const response = await fetch(
    `/api/accounts/account-1/verification-sessions/${encodeURIComponent(window.e2e.sessionId)}`,
    {headers: {Authorization: `Bearer ${controlToken}`}},
  );
  return {status: response.status, body: await response.json()};
};
(async () => {
  transition('creating_session');
  const createdResponse = await fetch('/api/accounts/account-1/verification-sessions', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${controlToken}`,
      'Idempotency-Key': 'remote-pointer-production-e2e',
    },
  });
  if (!createdResponse.ok) throw new Error(`create failed: ${createdResponse.status}`);
  const created = await createdResponse.json();
  window.e2e.sessionId = created.session_id;
  transition('minting_proof', {sessionId: created.session_id});
  const proofResponse = await fetch(
    `/api/accounts/account-1/verification-sessions/${encodeURIComponent(created.session_id)}/remote-proof`,
    {method: 'POST', headers: {Authorization: `Bearer ${controlToken}`}},
  );
  if (!proofResponse.ok) throw new Error(`proof failed: ${proofResponse.status}`);
  const access = await proofResponse.json();
  window.e2e.viewerUrl = access.viewer_url;
  window.e2e.websocketUrl = access.websocket_url;
  transition('viewer_loading', {
    viewerUrl: access.viewer_url,
    websocketUrl: access.websocket_url,
  });
  document.getElementById('viewer').src = access.viewer_url;
})().catch(error => {
  transition('failed', {error: String(error?.message || error)});
});
</script></body></html>"""


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def _validate_runtime_tmpfs(mounts_text: str) -> None:
    for line in mounts_text.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[1] != "/tmp":  # nosec B108
            continue
        filesystem = fields[2]
        options = set(fields[3].split(","))
        required_options = {"rw", "noexec", "nosuid"}
        missing = sorted(required_options - options)
        if filesystem != "tmpfs":
            raise RuntimeError(f"E2E /tmp must be tmpfs, found {filesystem}")
        if missing:
            raise RuntimeError(
                f"E2E /tmp is missing required mount options: {', '.join(missing)}"
            )
        return
    raise RuntimeError("E2E /tmp mount was not found")


def _wait_for_tcp(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"port {port} did not open")


class _ConnectionDiagnostics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []

    def record(self, layer: str, event: str, **details: object) -> None:
        entry = {
            "layer": layer,
            "event": event,
            "elapsed_ms": int(time.monotonic() * 1000),
            **details,
        }
        with self._lock:
            self._events.append(entry)
        print(
            json.dumps({"e2e_diagnostic": entry}, separators=(",", ":"), default=str),
            file=sys.stderr,
            flush=True,
        )

    def failure_message(
        self,
        reason: str,
        *,
        driver_state: object,
    ) -> str:
        with self._lock:
            events = [dict(event) for event in self._events]
        payload = {"driver_state": driver_state, "events": events}
        return f"{reason}: {json.dumps(payload, separators=(',', ':'), default=str)}"


def _websocket_handshake_details(
    raw_headers: Iterable[tuple[bytes, bytes]],
) -> dict[str, object]:
    headers = {
        name.decode("latin-1").lower(): value.decode("latin-1")
        for name, value in raw_headers
    }
    cookie_names = sorted(
        part.split("=", 1)[0].strip()
        for part in headers.get("cookie", "").split(";")
        if "=" in part and part.split("=", 1)[0].strip()
    )
    return {
        "host": headers.get("host", ""),
        "origin": headers.get("origin", ""),
        "sec_fetch_site": headers.get("sec-fetch-site", ""),
        "sec_fetch_mode": headers.get("sec-fetch-mode", ""),
        "sec_fetch_dest": headers.get("sec-fetch-dest", ""),
        "cookie_names": cookie_names,
    }


def _diagnostic_fetch_metadata_headers() -> dict[str, str]:
    if os.getenv("XIANYU_POINTER_E2E_DIAGNOSTIC_FETCH_METADATA") != "1":
        return {}
    return {
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "websocket",
        "Sec-Fetch-Dest": "empty",
    }


def _diagnostic_websocket_headers(
    raw_headers: Iterable[tuple[bytes, bytes]],
) -> list[tuple[bytes, bytes]]:
    headers = list(raw_headers)
    configured = _diagnostic_fetch_metadata_headers()
    if not configured:
        return headers
    existing = {name.lower() for name, _ in headers}
    headers.extend(
        (name.lower().encode("ascii"), value.encode("ascii"))
        for name, value in configured.items()
        if name.lower().encode("ascii") not in existing
    )
    return headers


class _WebSocketDiagnosticsMiddleware:
    def __init__(self, app: Any, *, diagnostics: _ConnectionDiagnostics) -> None:
        self._app = app
        self._diagnostics = diagnostics

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "websocket":
            raw_headers = list(scope.get("headers", ()))
            details = _websocket_handshake_details(raw_headers)
            self._diagnostics.record(
                "control.websocket",
                "handshake",
                path=scope.get("path", ""),
                query_string=bytes(scope.get("query_string", b"")).decode(
                    "latin-1", errors="replace"
                ),
                **details,
            )
            effective_headers = _diagnostic_websocket_headers(raw_headers)
            if effective_headers != raw_headers:
                self._diagnostics.record(
                    "control.websocket",
                    "diagnostic_fetch_metadata_injected",
                    **_websocket_handshake_details(effective_headers),
                )
                scope = {**scope, "headers": effective_headers}
        await self._app(scope, receive, send)


class _UvicornService:
    def __init__(
        self,
        app: FastAPI,
        port: int,
        *,
        name: str,
        diagnostics: _ConnectionDiagnostics,
        certificate_path: Path | None = None,
        key_path: Path | None = None,
    ) -> None:
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="error",
                access_log=False,
                lifespan="on",
                ssl_certfile=str(certificate_path) if certificate_path else None,
                ssl_keyfile=str(key_path) if key_path else None,
            )
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._name = name
        self._diagnostics = diagnostics
        self._port = port
        self._error: BaseException | None = None
        self._started = False

    def start(self) -> None:
        self._diagnostics.record(f"service.{self._name}", "starting", port=self._port)
        self._thread.start()
        self._started = True
        _wait_for_tcp(self._port)
        if self._error is not None:
            raise RuntimeError(f"server on port {self._port} failed") from self._error
        self._diagnostics.record(f"service.{self._name}", "tcp_ready", port=self._port)

    def close(self) -> None:
        if not self._started:
            return
        self._server.should_exit = True
        self._thread.join(timeout=30)
        if self._thread.is_alive():
            self._server.force_exit = True
            self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise TimeoutError(f"server on port {self._port} did not stop")
        if self._error is not None:
            raise RuntimeError(f"server on port {self._port} failed") from self._error
        self._diagnostics.record(f"service.{self._name}", "stopped", port=self._port)

    def snapshot(self) -> dict[str, object]:
        return {
            "name": self._name,
            "port": self._port,
            "started": self._started,
            "thread_alive": self._thread.is_alive(),
            "error": repr(self._error) if self._error is not None else None,
        }

    def _run(self) -> None:
        try:
            self._server.run()
        except BaseException as exc:
            self._error = exc
            self._diagnostics.record(
                f"service.{self._name}",
                "crashed",
                port=self._port,
                error=repr(exc),
            )


class _PointerProbe:
    def __init__(self, diagnostics: _ConnectionDiagnostics | None = None) -> None:
        self._condition = threading.Condition()
        self._events: list[dict[str, Any]] = []
        self._diagnostics = diagnostics

    def record(self, event: dict[str, Any]) -> None:
        with self._condition:
            self._events.append(event)
            self._condition.notify_all()
        if self._diagnostics is not None:
            self._diagnostics.record("pointer", "received", **event)

    def wait_for_drag(self, timeout: float) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                events = list(self._events)
                try:
                    _assert_pointer_drag(events)
                    return events
                except (AssertionError, StopIteration):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise AssertionError(
                            f"pointer drag did not reach challenge: {events!r}"
                        ) from None
                    self._condition.wait(timeout=remaining)


def _assert_pointer_drag(events: list[dict[str, Any]]) -> None:
    down_index = next(index for index, event in enumerate(events) if event["type"] == "pointerdown")
    move_indexes = [
        index
        for index, event in enumerate(events[down_index + 1 :], start=down_index + 1)
        if event["type"] == "pointermove" and event["buttons"] == 1
    ]
    if len(move_indexes) < 2:
        raise AssertionError(f"pointer drag requires multiple pointermove events: {events!r}")
    move_index = move_indexes[-1]
    up_index = next(
        index
        for index, event in enumerate(events[move_index + 1 :], start=move_index + 1)
        if event["type"] == "pointerup"
    )
    if not down_index < move_index < up_index:
        raise AssertionError(f"invalid pointer sequence: {events!r}")
    pressed_events = [events[down_index], *(events[index] for index in move_indexes)]
    horizontal_positions = [float(event["x"]) for event in pressed_events]
    horizontal_delta = max(horizontal_positions) - min(horizontal_positions)
    if horizontal_delta < MIN_HORIZONTAL_DRAG_DELTA:
        raise AssertionError(
            "pointer drag requires significant horizontal delta "
            f"({horizontal_delta:.1f} < {MIN_HORIZONTAL_DRAG_DELTA}): {events!r}"
        )


def _create_challenge_app(probe: _PointerProbe) -> FastAPI:
    app = FastAPI()

    @app.get("/verify", response_class=HTMLResponse)
    async def challenge() -> str:
        return _CHALLENGE_HTML

    @app.post("/events", status_code=204)
    async def pointer_event(request: Request) -> Response:
        payload = await request.json()
        if not isinstance(payload, dict) or payload.get("type") not in {
            "pointerdown",
            "pointermove",
            "pointerup",
        }:
            raise HTTPException(status_code=400, detail="invalid pointer event")
        probe.record(dict(payload))
        return Response(status_code=204)

    return app


def _create_control_app(
    database_path: Path,
    connector_origin: str,
    *,
    control_token: str,
    internal_token: str,
    diagnostics: _ConnectionDiagnostics,
) -> FastAPI:
    connector = ConnectorClient(connector_origin, internal_token)

    def current_user(request: Request) -> dict[str, Any]:
        if request.headers.get("Authorization") != f"Bearer {control_token}":
            raise HTTPException(status_code=401, detail="not authenticated")
        return {"user_id": 7, "username": "seller", "is_admin": False}

    app = FastAPI()
    app.add_middleware(_WebSocketDiagnosticsMiddleware, diagnostics=diagnostics)
    app.include_router(
        create_accounts_router(
            database_path,
            current_user,
            connector.create_qr_session,
            connector.get_qr_session,
            create_verification=connector.create_verification_session,
            get_verification=connector.get_verification_session,
            get_verification_frame=connector.get_verification_frame,
            send_verification_input=connector.send_verification_input,
            complete_verification=connector.complete_verification_session,
            cancel_verification=connector.cancel_verification_session,
            connect_remote_verification=connector.connect_remote_verification,
            remote_verification_enabled=True,
            remote_verification_public_origin=f"https://{CONTROL_HOST}",
            remote_verification_proof_secret=internal_token,
        )
    )
    app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")

    @app.get("/e2e", response_class=HTMLResponse)
    async def driver() -> str:
        return _DRIVER_HTML.replace("__CONTROL_TOKEN_JSON__", json.dumps(control_token))

    return app


def _initialize_database(database_path: Path, master_key_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL);
            CREATE TABLE cookies (id TEXT PRIMARY KEY, value TEXT NOT NULL, user_id INTEGER NOT NULL);
            CREATE TABLE cookie_status (cookie_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
            INSERT INTO users (id, username) VALUES (7, 'seller');
            INSERT INTO cookies (id, value, user_id) VALUES ('account-1', 'e2e', 7);
            INSERT INTO cookie_status (cookie_id, enabled) VALUES ('account-1', 0);
            """
        )
        apply_connector_schema(connection)
    key = b"k" * 32
    master_key_path.write_bytes(base64.urlsafe_b64encode(key))
    RuntimeService(SqliteRuntimeRepository(database_path)).transition_to(
        ACCOUNT_ID,
        AccountState.MANUAL_VERIFICATION_REQUIRED,
    )
    SqliteSecretRepository(database_path, SecretCipher(key)).save(
        ACCOUNT_ID,
        "verification_url",
        f"https://{CHALLENGE_HOST}/verify",
    )


def _write_certificate(root: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CONTROL_HOST)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(CONTROL_HOST), x509.DNSName(CHALLENGE_HOST)]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    certificate_path = root / "e2e-cert.pem"
    key_path = root / "e2e-key.pem"
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


def _browser_wrapper_environment(
    chromium: Path,
    *,
    browser_state_root: Path,
    control_port: int,
    challenge_port: int,
) -> dict[str, str]:
    resolver_rules = (
        f"MAP {CONTROL_HOST}:443 127.0.0.1:{control_port},"
        f"MAP {CHALLENGE_HOST}:443 127.0.0.1:{challenge_port}"
    )
    return {
        "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH": str(RUNNER_PATH),
        "XIANYU_POINTER_E2E_CHROMIUM": str(chromium),
        "XIANYU_POINTER_E2E_BROWSER_STATE": str(browser_state_root),
        "XIANYU_POINTER_E2E_HOST_RESOLVER_RULES": resolver_rules,
    }


def _websocket_payload_size(payload: object) -> int:
    if isinstance(payload, bytes):
        return len(payload)
    return len(str(payload).encode("utf-8"))


def _observe_websocket(websocket: Any, diagnostics: _ConnectionDiagnostics) -> None:
    diagnostics.record("websocket", "opened", url=websocket.url)
    websocket.on(
        "framesent",
        lambda payload: diagnostics.record(
            "websocket",
            "frame_sent",
            url=websocket.url,
            bytes=_websocket_payload_size(payload),
        ),
    )
    websocket.on(
        "framereceived",
        lambda payload: diagnostics.record(
            "websocket",
            "frame_received",
            url=websocket.url,
            bytes=_websocket_payload_size(payload),
        ),
    )
    websocket.on("close", lambda: diagnostics.record("websocket", "closed", url=websocket.url))
    websocket.on(
        "socketerror",
        lambda error: diagnostics.record(
            "websocket",
            "error",
            url=websocket.url,
            error=str(error),
        ),
    )


def _observe_page(page: Any, diagnostics: _ConnectionDiagnostics) -> None:
    page.on(
        "console",
        lambda message: diagnostics.record(
            "browser.console",
            message.type,
            text=message.text,
        ),
    )
    page.on(
        "pageerror",
        lambda error: diagnostics.record("browser.page", "error", error=str(error)),
    )
    page.on(
        "requestfailed",
        lambda request: diagnostics.record(
            "http",
            "request_failed",
            url=request.url,
            failure=request.failure,
        ),
    )
    page.on(
        "response",
        lambda response: diagnostics.record(
            "http",
            "response",
            url=response.url,
            status=response.status,
        ),
    )
    page.on("websocket", lambda websocket: _observe_websocket(websocket, diagnostics))


def _driver_snapshot(page: Any) -> object:
    try:
        return page.evaluate(
            """async () => {
              const state = JSON.parse(JSON.stringify(window.e2e || {}));
              try {
                const sessionProbe = await window.e2eProbeSession?.();
                if (sessionProbe) {
                  state.sessionStatus = sessionProbe.status;
                  state.session = sessionProbe.body;
                }
              } catch (error) {
                state.sessionProbeError = String(error?.message || error);
              }
              return state;
            }"""
        )
    except Exception as exc:
        return {"snapshot_error": repr(exc)}


def _wait_for_viewer_connection(
    page: Any,
    diagnostics: _ConnectionDiagnostics,
    services: tuple[_UvicornService, ...],
) -> dict[str, Any]:
    try:
        page.wait_for_function(
            "['connected', 'failed', 'closed', 'disconnected'].includes(window.e2e?.state)",
            timeout=45_000,
        )
    except PlaywrightTimeoutError as exc:
        state = _driver_snapshot(page)
        for service in services:
            diagnostics.record("service", "snapshot", **service.snapshot())
        if isinstance(state, dict) and isinstance(state.get("session"), dict):
            diagnostics.record("rfb", "session_state", **state["session"])
        raise AssertionError(
            diagnostics.failure_message(
                "remote viewer connection timed out",
                driver_state=state,
            )
        ) from exc
    state = _driver_snapshot(page)
    if not isinstance(state, dict):
        raise AssertionError(
            diagnostics.failure_message("invalid driver state", driver_state=state)
        )
    if isinstance(state.get("session"), dict):
        diagnostics.record("rfb", "session_state", **state["session"])
    if state.get("state") != "connected":
        raise AssertionError(
            diagnostics.failure_message("production viewer failed", driver_state=state)
        )
    return state


@contextmanager
def _temporary_environment(overrides: dict[str, str]) -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main() -> int:
    _validate_runtime_tmpfs(Path("/proc/mounts").read_text(encoding="utf-8"))
    required = ("xvfb-run", "x11vnc")
    executables = {command: shutil.which(command) for command in required}
    missing = [command for command, executable in executables.items() if executable is None]
    if missing:
        raise RuntimeError(f"missing E2E commands: {', '.join(missing)}")

    chromium = resolve_production_chromium_executable()
    control_token = secrets.token_urlsafe(32)
    internal_token = secrets.token_urlsafe(32)
    diagnostics = _ConnectionDiagnostics()
    probe = _PointerProbe(diagnostics)
    with tempfile.TemporaryDirectory(prefix="xianyu-rfb-e2e-") as temporary_directory:
        root = Path(temporary_directory)
        database_path = root / "e2e.db"
        master_key_path = root / "master.key"
        profiles_root = root / "profiles"
        connector_port = _reserve_port()
        control_port = _reserve_port()
        challenge_port = _reserve_port()
        certificate_path, key_path = _write_certificate(root)
        browser_environment = _browser_wrapper_environment(
            chromium,
            browser_state_root=root / "browser-state",
            control_port=control_port,
            challenge_port=challenge_port,
        )
        _initialize_database(database_path, master_key_path)

        settings = ConnectorSettings(
            database_path=database_path,
            profiles_root=profiles_root,
            master_key_path=master_key_path,
            internal_api_token=internal_token,
            remote_verification_enabled=True,
        )
        challenge = _UvicornService(
            _create_challenge_app(probe),
            challenge_port,
            name="challenge",
            diagnostics=diagnostics,
            certificate_path=certificate_path,
            key_path=key_path,
        )
        connector = _UvicornService(
            create_connector_app(settings),
            connector_port,
            name="connector",
            diagnostics=diagnostics,
        )
        control = _UvicornService(
            _create_control_app(
                database_path,
                f"http://127.0.0.1:{connector_port}",
                control_token=control_token,
                internal_token=internal_token,
                diagnostics=diagnostics,
            ),
            control_port,
            name="control",
            diagnostics=diagnostics,
            certificate_path=certificate_path,
            key_path=key_path,
        )
        environment = {
            "DB_PATH": str(database_path),
            "XIANYU_MASTER_KEY_PATH": str(master_key_path),
            "XIANYU_PROFILES_ROOT": str(profiles_root),
            "XIANYU_XVFB_RUN": str(executables["xvfb-run"]),
            "XIANYU_X11VNC_EXECUTABLE": str(executables["x11vnc"]),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            **browser_environment,
        }

        with _temporary_environment(environment):
            try:
                challenge.start()
                connector.start()
                control.start()
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        headless=True,
                        executable_path=str(RUNNER_PATH),
                        args=["--disable-dev-shm-usage"],
                    )
                    page = browser.new_page(
                        viewport={"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT + 80}
                    )
                    requested_urls: list[str] = []
                    websocket_urls: list[str] = []
                    page.on("request", lambda request: requested_urls.append(request.url))
                    page.on("websocket", lambda websocket: websocket_urls.append(websocket.url))
                    _observe_page(page, diagnostics)
                    diagnostics.record("browser", "driver_navigate", url=f"https://{CONTROL_HOST}/e2e")
                    page.goto(f"https://{CONTROL_HOST}/e2e", wait_until="domcontentloaded")
                    state = _wait_for_viewer_connection(
                        page,
                        diagnostics,
                        (challenge, connector, control),
                    )

                    viewer = page.frame_locator("#viewer")
                    canvas = viewer.locator("canvas")
                    canvas.wait_for(state="visible", timeout=10_000)
                    box = canvas.bounding_box()
                    if box is None or box["width"] < 100 or box["height"] < 100:
                        raise AssertionError(f"invalid noVNC canvas bounds: {box!r}")

                    if not any("/static/js/remote-verification-viewer.js" in url for url in requested_urls):
                        raise AssertionError("production remote verification viewer was not loaded")
                    if not any("/static/vendor/novnc/core/rfb.js" in url for url in requested_urls):
                        raise AssertionError("production noVNC RFB module was not loaded")
                    if not any(url.endswith(str(state["websocketUrl"])) for url in websocket_urls):
                        raise AssertionError(f"control remote WebSocket was not opened: {websocket_urls!r}")

                    start_x = box["x"] + box["width"] * 0.3
                    end_x = box["x"] + box["width"] * 0.7
                    drag_y = box["y"] + box["height"] * 0.65
                    page.mouse.move(start_x, drag_y)
                    page.mouse.down()
                    page.mouse.move(end_x, drag_y, steps=12)
                    page.mouse.up()

                    events = probe.wait_for_drag(10)
                    _assert_pointer_drag(events)
                    print(
                        json.dumps(
                            {
                                "result": "ok",
                                "session_id": state["sessionId"],
                                "control_websocket": state["websocketUrl"],
                                "events": events,
                            },
                            separators=(",", ":"),
                        )
                    )
                    browser.close()
            finally:
                cleanup_errors: list[Exception] = []
                for service in (control, connector, challenge):
                    try:
                        service.close()
                    except Exception as exc:
                        cleanup_errors.append(exc)
                if cleanup_errors:
                    raise ExceptionGroup("E2E service cleanup failed", cleanup_errors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
