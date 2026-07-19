from __future__ import annotations

import asyncio
import base64
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xianyu_connector.api import _supervisor_loop, create_connector_app
from xianyu_connector.application.qr_auth_manager import QrAuthManager
from xianyu_connector.application.runtime_reporter import RuntimeReporter
from xianyu_connector.application.runtime_service import RuntimeService
from xianyu_connector.application.verification_runtime import VerificationCoordinator
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository
from xianyu_connector.settings import ConnectorSettings


def _settings(tmp_path: Path, *, remote_verification_enabled: bool = False) -> ConnectorSettings:
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
        remote_verification_enabled=remote_verification_enabled,
    )


def test_connector_live_ready_and_internal_auth(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_connector_app(settings)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert (
            client.get(
                "/internal/accounts/account-1/qr-sessions/missing",
                headers={"X-Connector-Token": "wrong"},
            ).status_code
            == 401
        )

        repository = SqliteRuntimeRepository(settings.database_path)
        RuntimeService(repository).transition_to("account-1", AccountState.AUTHENTICATING)
        reporter = RuntimeReporter("account-1", repository)
        reporter.mark_session(True)
        reporter.mark_token(True)
        reporter.mark_websocket(True)
        reporter.mark_heartbeat()

        ready = client.get("/health/ready")
        missing = client.get(
            "/internal/accounts/account-1/qr-sessions/missing",
            headers={"X-Connector-Token": "t" * 32},
        )

    assert ready.status_code == 200
    assert ready.json()["online_accounts"] == ["account-1"]
    assert missing.status_code == 404


def test_connector_live_fails_when_migration_disappears(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_connector_app(settings)

    with TestClient(app) as client:
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("DROP TABLE account_runtime_states")

        response = client.get("/health/live")

    assert response.status_code == 503
    assert response.json()["detail"] == "connector migration missing"


@pytest.mark.asyncio
async def test_qr_create_blocks_verification_create_for_same_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qr_started = asyncio.Event()
    release_qr = asyncio.Event()
    verification_started = asyncio.Event()

    async def gated_qr_create(
        self: QrAuthManager,
        account_id: str,
        user_id: int,
    ) -> dict[str, object]:
        del self, user_id
        qr_started.set()
        await release_qr.wait()
        return {
            "success": True,
            "session_id": "qr-1",
            "qr_code_url": "data:image/png;base64,AA==",
            "status": "waiting",
            "account_id": account_id,
        }

    def record_verification_create(
        self: VerificationCoordinator,
        account_id: str,
        operator_id: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        del self, operator_id, idempotency_key
        verification_started.set()
        return {"session_id": "verify-1", "account_id": account_id}

    monkeypatch.setattr(QrAuthManager, "create", gated_qr_create)
    monkeypatch.setattr(VerificationCoordinator, "create", record_verification_create)
    app = create_connector_app(_settings(tmp_path, remote_verification_enabled=True))
    headers = {"X-Connector-Token": "t" * 32}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://connector") as client:
        qr_task = asyncio.create_task(
            client.post(
                "/internal/accounts/account-1/qr-sessions",
                headers=headers,
                json={"user_id": 7},
            )
        )
        await qr_started.wait()
        verification_task = asyncio.create_task(
            client.post(
                "/internal/accounts/account-1/verification-sessions",
                headers=headers,
                json={"user_id": 7, "idempotency_key": "verification-after-qr"},
            )
        )
        await asyncio.sleep(0.01)
        assert not verification_task.done()
        assert not verification_started.is_set()

        release_qr.set()
        qr_response, verification_response = await asyncio.gather(
            qr_task,
            verification_task,
        )

    assert qr_response.status_code == 200
    assert verification_response.status_code == 200
    assert verification_started.is_set()


async def _assert_request_does_not_block_event_loop(
    request: object,
    entered: threading.Event,
    release: threading.Event,
) -> object:
    def delayed_release() -> None:
        assert entered.wait(timeout=1)
        time.sleep(0.2)
        release.set()

    releaser = threading.Thread(target=delayed_release, daemon=True)
    releaser.start()
    task = asyncio.create_task(request)  # type: ignore[arg-type]
    await asyncio.to_thread(entered.wait, 1)
    loop_tick = asyncio.Event()
    asyncio.get_running_loop().call_soon(loop_tick.set)
    await asyncio.wait_for(loop_tick.wait(), timeout=0.05)
    assert release.is_set() is False
    release.set()
    response = await task
    releaser.join(timeout=1)
    return response


@pytest.mark.asyncio
async def test_verification_create_does_not_block_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_create(
        self: VerificationCoordinator,
        account_id: str,
        operator_id: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        del self, operator_id, idempotency_key
        entered.set()
        assert release.wait(timeout=1)
        return {"session_id": "verify-1", "account_id": account_id}

    monkeypatch.setattr(VerificationCoordinator, "create", blocking_create)
    app = create_connector_app(_settings(tmp_path, remote_verification_enabled=True))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://connector") as client:
        response = await _assert_request_does_not_block_event_loop(
            client.post(
                "/internal/accounts/account-1/verification-sessions",
                headers={"X-Connector-Token": "t" * 32},
                json={"user_id": 7, "idempotency_key": "nonblocking-create"},
            ),
            entered,
            release,
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_verification_complete_does_not_block_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_complete(
        self: VerificationCoordinator,
        account_id: str,
        session_id: str,
        ticket: str | None,
    ) -> dict[str, object]:
        del self, ticket
        entered.set()
        assert release.wait(timeout=1)
        return {"session_id": session_id, "account_id": account_id, "state": "verifying"}

    monkeypatch.setattr(VerificationCoordinator, "complete", blocking_complete)
    app = create_connector_app(_settings(tmp_path, remote_verification_enabled=True))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://connector") as client:
        response = await _assert_request_does_not_block_event_loop(
            client.post(
                "/internal/accounts/account-1/verification-sessions/verify-1/complete",
                headers={"X-Connector-Token": "t" * 32},
            ),
            entered,
            release,
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_supervisor_loop_survives_transient_supervise_failure() -> None:
    recovered = asyncio.Event()

    class FlakySupervisor:
        calls = 0

        def process_next_command(self) -> None:
            return None

        def supervise(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise OSError("temporary supervisor failure")
            recovered.set()

    app = type("App", (), {"state": type("State", (), {})()})()
    app.state.last_supervisor_tick = 0.0
    task = asyncio.create_task(_supervisor_loop(app, FlakySupervisor()))  # type: ignore[arg-type]
    try:
        await asyncio.wait_for(recovered.wait(), timeout=2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert recovered.is_set()


@pytest.mark.asyncio
async def test_supervisor_loop_offloads_blocking_tick_from_event_loop() -> None:
    entered = threading.Event()
    release = threading.Event()

    class SlowSupervisor:
        def process_next_command(self) -> None:
            entered.set()
            assert release.wait(timeout=1)
            return None

        def supervise(self) -> None:
            return None

    app = FastAPI()
    app.state.last_supervisor_tick = 0.0

    @app.get("/health/probe")
    async def health_probe() -> dict[str, str]:
        return {"status": "healthy"}

    releaser = threading.Timer(0.5, release.set)
    releaser.start()
    started_at = time.monotonic()
    task = asyncio.create_task(_supervisor_loop(app, SlowSupervisor()))  # type: ignore[arg-type]
    try:
        await asyncio.to_thread(entered.wait, 1)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://connector") as client:
            response = await asyncio.wait_for(client.get("/health/probe"), timeout=0.1)
        assert response.status_code == 200
        assert time.monotonic() - started_at < 0.25
        loop_tick = asyncio.Event()
        asyncio.get_running_loop().call_soon(loop_tick.set)
        await asyncio.wait_for(loop_tick.wait(), timeout=0.05)
        assert release.is_set() is False
    finally:
        release.set()
        releaser.cancel()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_supervisor_loop_cancellation_waits_for_inflight_tick() -> None:
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class SlowSupervisor:
        def process_next_command(self) -> None:
            entered.set()
            try:
                assert release.wait(timeout=1)
            finally:
                finished.set()
            return None

        def supervise(self) -> None:
            return None

    app = FastAPI()
    app.state.last_supervisor_tick = 0.0
    task = asyncio.create_task(_supervisor_loop(app, SlowSupervisor()))  # type: ignore[arg-type]
    await asyncio.to_thread(entered.wait, 1)

    task.cancel()
    await asyncio.sleep(0.05)

    assert task.done() is False
    assert finished.is_set() is False

    release.set()
    await asyncio.gather(task, return_exceptions=True)
    assert finished.is_set()
