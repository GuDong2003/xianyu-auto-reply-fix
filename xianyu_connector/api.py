from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, WebSocket
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict

from xianyu_connector.application.account_operation_coordinator import AccountOperationCoordinator
from xianyu_connector.application.account_supervisor import AccountSupervisor
from xianyu_connector.application.local_verification_handoff import (
    LocalHandoffConflict,
    LocalHandoffDisabled,
    LocalHandoffGone,
    LocalHandoffNotFound,
    LocalHandoffOperatorMismatch,
    LocalVerificationHandoff,
)
from xianyu_connector.application.qr_auth_manager import QrAuthManager
from xianyu_connector.application.verification_runtime import (
    VerificationCoordinator,
    VerificationRfbForbidden,
    VerificationUnavailable,
)
from xianyu_connector.application.verification_session_manager import (
    InvalidVerificationToken,
    VerificationSessionNotFound,
)
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.domain.commands import AccountCommand
from xianyu_connector.egress_guard import verify_fixed_egress
from xianyu_connector.infrastructure.legacy_account_catalog import LegacyAccountCatalog
from xianyu_connector.infrastructure.local_handoff_repository import (
    InvalidLocalHandoffToken,
)
from xianyu_connector.infrastructure.sqlite_command_repository import SqliteCommandRepository
from xianyu_connector.infrastructure.sqlite_health_probe import (
    DatabaseHealthError,
    SqliteHealthProbe,
)
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository
from xianyu_connector.infrastructure.verification_rfb import RfbWebSocketBridge
from xianyu_connector.infrastructure.worker_process import WorkerProcessFactory
from xianyu_connector.settings import ConnectorSettings

logger = logging.getLogger(__name__)


def create_connector_app(settings: ConnectorSettings) -> FastAPI:
    settings.validate()
    runtime_repository = SqliteRuntimeRepository(settings.database_path)
    command_repository = SqliteCommandRepository(settings.database_path)
    database_health = SqliteHealthProbe(settings.database_path)
    operation_coordinator = AccountOperationCoordinator()
    supervisor = AccountSupervisor(
        runtime_repository,
        command_repository,
        WorkerProcessFactory(
            settings.database_path,
            settings.profiles_root,
            settings.master_key_path,
            shadow_mode=settings.shadow_mode,
        ),
        coordinator=operation_coordinator,
    )
    verification = VerificationCoordinator(
        settings.database_path,
        settings.profiles_root,
        settings.master_key_path,
        runtime_repository,
        command_repository,
        coordinator=operation_coordinator,
    )
    qr_auth = QrAuthManager(
        settings.database_path,
        settings.profiles_root,
        settings.master_key_path,
        command_repository,
        runtime_repository,
        stop_verification=verification.stop_for_qr,
        coordinator=operation_coordinator,
    )
    rfb_bridge = RfbWebSocketBridge()
    account_operation_locks: dict[str, asyncio.Lock] = {}
    local_handoff = LocalVerificationHandoff(
        settings.database_path,
        settings.master_key_path,
        runtime_repository,
        command_repository,
        enabled=settings.local_verification_handoff_enabled,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.last_supervisor_tick = time.monotonic()
        if settings.require_fixed_egress:
            verify_fixed_egress(
                settings.expected_egress_ip,
                settings.egress_check_url,
            )
        command_repository.recover_interrupted()
        _enqueue_bootstrap_commands(settings, command_repository)
        task = asyncio.create_task(_supervisor_loop(app, supervisor))
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await qr_auth.close()
            local_handoff.close()
            verification.close()
            supervisor.stop_all()

    app = FastAPI(title="Xianyu Connector", lifespan=lifespan)
    app.state.runtime_repository = runtime_repository

    def require_internal_token(
        token: str = Header(alias="X-Connector-Token"),
    ) -> None:
        if not secrets.compare_digest(token, settings.internal_api_token):
            raise HTTPException(status_code=401, detail="invalid connector token")

    @app.get("/health/live")
    async def health_live() -> dict[str, object]:
        age = time.monotonic() - app.state.last_supervisor_tick
        if age > 30:
            raise HTTPException(status_code=503, detail="supervisor stalled")
        try:
            database_health.verify()
        except DatabaseHealthError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"status": "healthy", "supervisor_age_seconds": round(age, 3)}

    @app.get("/health/ready")
    async def health_ready() -> dict[str, object]:
        runtimes = runtime_repository.list_all()
        online = [
            runtime.account_id
            for runtime in runtimes
            if runtime.state is AccountState.ONLINE and runtime.readiness.online
        ]
        if not online:
            raise HTTPException(status_code=503, detail={"online_accounts": []})
        return {"status": "ready", "online_accounts": online}

    @app.post("/internal/accounts/{account_id}/qr-sessions")
    async def create_qr_session(
        account_id: str,
        request: InternalQrRequest,
        token: str = Header(alias="X-Connector-Token"),
    ) -> dict[str, object]:
        require_internal_token(token)
        async with account_operation_locks.setdefault(account_id, asyncio.Lock()):
            return await qr_auth.create(account_id, request.user_id)

    @app.get("/internal/accounts/{account_id}/qr-sessions/{session_id}")
    async def get_qr_session(
        account_id: str,
        session_id: str,
        token: str = Header(alias="X-Connector-Token"),
    ) -> dict[str, object]:
        require_internal_token(token)
        payload = qr_auth.get(session_id, account_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="qr session not found")
        return payload

    @app.post("/internal/accounts/{account_id}/verification-sessions")
    async def create_verification_session(
        account_id: str,
        request: InternalVerificationRequest,
        token: str = Header(alias="X-Connector-Token"),
    ) -> dict[str, object]:
        require_internal_token(token)
        if not settings.remote_verification_enabled:
            raise HTTPException(status_code=404, detail="remote verification unavailable")
        async with account_operation_locks.setdefault(account_id, asyncio.Lock()):
            try:
                return await verification.create_async(
                    account_id,
                    str(request.user_id),
                    request.idempotency_key,
                )
            except VerificationUnavailable as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/internal/accounts/{account_id}/local-verification-sessions")
    async def create_local_verification_session(
        account_id: str,
        request: InternalVerificationRequest,
        token: str = Header(alias="X-Connector-Token"),
    ) -> dict[str, object]:
        require_internal_token(token)
        try:
            return local_handoff.create(
                account_id,
                str(request.user_id),
                request.idempotency_key,
            )
        except LocalHandoffDisabled as exc:
            raise HTTPException(status_code=404, detail="local handoff unavailable") from exc
        except LocalHandoffConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid verification challenge") from exc

    @app.get(
        "/internal/accounts/{account_id}/local-verification-sessions/{session_id}/handoff",
        response_class=RedirectResponse,
    )
    async def consume_local_verification_handoff(
        account_id: str,
        session_id: str,
        ticket: str = Header(alias="X-Verification-Ticket"),
        token: str = Header(alias="X-Connector-Token"),
    ) -> RedirectResponse:
        require_internal_token(token)
        try:
            challenge_url = local_handoff.consume(account_id, session_id, ticket)
        except LocalHandoffDisabled as exc:
            raise HTTPException(status_code=404, detail="local handoff unavailable") from exc
        except LocalHandoffNotFound as exc:
            raise HTTPException(status_code=404, detail="local handoff not found") from exc
        except LocalHandoffGone as exc:
            raise HTTPException(status_code=410, detail="local handoff is gone") from exc
        except InvalidLocalHandoffToken as exc:
            raise HTTPException(status_code=401, detail="invalid local handoff token") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid verification challenge") from exc
        return RedirectResponse(
            challenge_url,
            status_code=302,
            headers={
                "Cache-Control": "no-store, private",
                "Pragma": "no-cache",
                "Referrer-Policy": "no-referrer",
            },
        )

    @app.post(
        "/internal/accounts/{account_id}/local-verification-sessions/{session_id}/complete"
    )
    async def complete_local_verification_handoff(
        account_id: str,
        session_id: str,
        request: InternalLocalVerificationComplete,
        operator_id: str = Header(alias="X-Operator-Id"),
        token: str = Header(alias="X-Connector-Token"),
    ) -> dict[str, object]:
        del request
        require_internal_token(token)
        try:
            return local_handoff.complete(account_id, session_id, operator_id)
        except LocalHandoffDisabled as exc:
            raise HTTPException(status_code=404, detail="local handoff unavailable") from exc
        except LocalHandoffNotFound as exc:
            raise HTTPException(status_code=404, detail="local handoff not found") from exc
        except LocalHandoffOperatorMismatch as exc:
            raise HTTPException(status_code=403, detail="local handoff operator mismatch") from exc
        except LocalHandoffConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/internal/accounts/{account_id}/verification-sessions/{session_id}")
    async def get_verification_session(
        account_id: str,
        session_id: str,
        after_seq: int = 0,
        ticket: str | None = Header(default=None, alias="X-Verification-Ticket"),
        token: str = Header(alias="X-Connector-Token"),
    ) -> dict[str, object]:
        require_internal_token(token)
        try:
            return verification.get(account_id, session_id, ticket, max(0, after_seq))
        except VerificationSessionNotFound as exc:
            raise HTTPException(status_code=404, detail="verification session not found") from exc
        except InvalidVerificationToken as exc:
            raise HTTPException(status_code=401, detail="invalid verification ticket") from exc
        except VerificationUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/internal/accounts/{account_id}/verification-sessions/{session_id}/frame")
    async def get_verification_frame(
        account_id: str,
        session_id: str,
        after_seq: int = 0,
        ticket: str | None = Header(default=None, alias="X-Verification-Ticket"),
        token: str = Header(alias="X-Connector-Token"),
    ) -> dict[str, object]:
        require_internal_token(token)
        try:
            return verification.frame(account_id, session_id, ticket, max(0, after_seq))
        except VerificationSessionNotFound as exc:
            raise HTTPException(status_code=404, detail="verification session not found") from exc
        except InvalidVerificationToken as exc:
            raise HTTPException(status_code=401, detail="invalid verification ticket") from exc
        except VerificationUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/internal/accounts/{account_id}/verification-sessions/{session_id}/input")
    async def send_verification_input(
        account_id: str,
        session_id: str,
        request: InternalVerificationInput,
        ticket: str | None = Header(default=None, alias="X-Verification-Ticket"),
        token: str = Header(alias="X-Connector-Token"),
    ) -> dict[str, object]:
        require_internal_token(token)
        try:
            return verification.input(
                account_id,
                session_id,
                ticket,
                request.model_dump(),
            )
        except VerificationSessionNotFound as exc:
            raise HTTPException(status_code=404, detail="verification session not found") from exc
        except InvalidVerificationToken as exc:
            raise HTTPException(status_code=401, detail="invalid verification ticket") from exc
        except VerificationUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.websocket(
        "/internal/accounts/{account_id}/verification-sessions/{session_id}/rfb"
    )
    async def bridge_verification_rfb(
        websocket: WebSocket,
        account_id: str,
        session_id: str,
    ) -> None:
        if not settings.remote_verification_enabled:
            await websocket.close(code=4404, reason="remote verification unavailable")
            return
        token = websocket.headers.get("x-connector-token", "")
        if not token or not secrets.compare_digest(token, settings.internal_api_token):
            await websocket.close(code=4401, reason="invalid connector token")
            return
        operator_id = websocket.headers.get("x-operator-id", "").strip()
        if not operator_id:
            await websocket.close(code=4403, reason="operator scope is required")
            return
        try:
            lease = verification.open_rfb(account_id, session_id, operator_id)
        except VerificationSessionNotFound:
            await websocket.close(code=4404, reason="verification session not found")
            return
        except VerificationRfbForbidden:
            await websocket.close(code=4403, reason="verification operator mismatch")
            return
        except VerificationUnavailable:
            await websocket.close(code=4409, reason="verification RFB unavailable")
            return
        except RuntimeError:
            await websocket.close(code=4409, reason="verification RFB already connected")
            return
        await rfb_bridge.relay(websocket, lease)

    @app.post("/internal/accounts/{account_id}/verification-sessions/{session_id}/complete")
    async def complete_verification_session(
        account_id: str,
        session_id: str,
        ticket: str | None = Header(default=None, alias="X-Verification-Ticket"),
        token: str = Header(alias="X-Connector-Token"),
    ) -> dict[str, object]:
        require_internal_token(token)
        try:
            return await verification.complete_async(account_id, session_id, ticket)
        except VerificationSessionNotFound as exc:
            raise HTTPException(status_code=404, detail="verification session not found") from exc
        except InvalidVerificationToken as exc:
            raise HTTPException(status_code=401, detail="invalid verification ticket") from exc
        except VerificationUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/internal/accounts/{account_id}/verification-sessions/{session_id}")
    async def cancel_verification_session(
        account_id: str,
        session_id: str,
        ticket: str | None = Header(default=None, alias="X-Verification-Ticket"),
        token: str = Header(alias="X-Connector-Token"),
    ) -> dict[str, object]:
        require_internal_token(token)
        try:
            return verification.cancel(account_id, session_id, ticket)
        except VerificationSessionNotFound as exc:
            raise HTTPException(status_code=404, detail="verification session not found") from exc
        except InvalidVerificationToken as exc:
            raise HTTPException(status_code=401, detail="invalid verification ticket") from exc
        except VerificationUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app


class InternalQrRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int


class InternalVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    idempotency_key: str


class InternalVerificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["down", "move", "up", "click"]
    x: float
    y: float
    button: Literal["left"] = "left"


class InternalLocalVerificationComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _supervisor_loop(app: FastAPI, supervisor: AccountSupervisor) -> None:
    while True:
        try:
            tick = asyncio.create_task(asyncio.to_thread(_run_supervisor_tick, supervisor))
            try:
                await asyncio.shield(tick)
            except asyncio.CancelledError:
                await asyncio.gather(tick, return_exceptions=True)
                raise
            app.state.last_supervisor_tick = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("account supervisor iteration failed")
        await asyncio.sleep(1)


def _run_supervisor_tick(supervisor: AccountSupervisor) -> None:
    while supervisor.process_next_command():
        pass
    supervisor.supervise()


def _enqueue_bootstrap_commands(
    settings: ConnectorSettings,
    commands: SqliteCommandRepository,
) -> None:
    boot_id = uuid.uuid4().hex
    for account_id in LegacyAccountCatalog(settings.database_path).enabled_account_ids():
        commands.enqueue(
            account_id,
            AccountCommand.START,
            f"bootstrap:{boot_id}:{account_id}",
            {"shadow_mode": settings.shadow_mode},
        )
