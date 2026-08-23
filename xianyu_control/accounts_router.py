from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, WebSocket
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from xianyu_connector.domain.account_state import AccountRuntime, AccountState
from xianyu_connector.domain.commands import AccountCommand
from xianyu_connector.infrastructure.sqlite_command_repository import SqliteCommandRepository
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository
from xianyu_control.account_access import AccountAccessRepository
from xianyu_control.connector_client import ConnectorHttpError
from xianyu_control.remote_verification_proof import (
    OperatorProofSigner,
    ProofStatus,
)
from xianyu_control.remote_verification_proxy import (
    RemoteVerificationRegistry,
    RemoteVerificationSocket,
    bridge_remote_verification,
)

LOCAL_HANDOFF_GRANT_TTL_SECONDS = 60
REMOTE_VERIFICATION_PROOF_TTL_SECONDS = 300
REMOTE_VIEWER_HTML = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>人工验证</title><link rel="stylesheet" href="/static/css/remote-verification-viewer.css?v=20260719-rfb1"></head>
<body><main id="remoteVerificationViewport" aria-label="闲鱼人工验证画面"></main><p id="remoteVerificationStatus" role="status" aria-live="polite">正在连接验证浏览器...</p>
<script type="module" src="/static/js/remote-verification-viewer.js?v=20260719-rfb1"></script></body>
</html>"""


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: AccountCommand
    payload: dict[str, Any] = Field(default_factory=dict)


class VerificationInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["down", "move", "up"]
    x: int = Field(ge=0, le=10000)
    y: int = Field(ge=0, le=10000)
    button: Literal["left"] = "left"


class VerificationCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    reason: str | None = Field(default=None, max_length=200)


class LocalVerificationCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RemoteVerificationProofRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


QrGenerate = Callable[[dict[str, Any], str | None], Awaitable[dict[str, Any]]]
QrCheck = Callable[[str, dict[str, Any], str], Awaitable[dict[str, Any]]]
VerificationCreate = Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]]
VerificationGet = Callable[..., Awaitable[dict[str, Any]]]
VerificationFrame = Callable[..., Awaitable[dict[str, Any]]]
VerificationInput = Callable[..., Awaitable[dict[str, Any]]]
VerificationComplete = Callable[..., Awaitable[dict[str, Any]]]
VerificationCancel = Callable[..., Awaitable[dict[str, Any]]]
LocalVerificationCreate = Callable[..., Awaitable[dict[str, Any]]]
LocalVerificationHandoff = Callable[..., Awaitable[str]]
LocalVerificationComplete = Callable[..., Awaitable[dict[str, Any]]]
RemoteVerificationConnect = Callable[
    ...,
    AbstractAsyncContextManager[RemoteVerificationSocket],
]


def create_accounts_router(
    database_path: Path,
    current_user_dependency: Callable[..., dict[str, Any]],
    generate_qr: QrGenerate,
    check_qr: QrCheck,
    *,
    create_verification: VerificationCreate | None = None,
    get_verification: VerificationGet | None = None,
    get_verification_frame: VerificationFrame | None = None,
    send_verification_input: VerificationInput | None = None,
    complete_verification: VerificationComplete | None = None,
    cancel_verification: VerificationCancel | None = None,
    create_local_verification: LocalVerificationCreate | None = None,
    get_local_verification_handoff: LocalVerificationHandoff | None = None,
    complete_local_verification: LocalVerificationComplete | None = None,
    local_verification_enabled: bool | None = None,
    local_verification_public_origin: str | None = None,
    connect_remote_verification: RemoteVerificationConnect | None = None,
    remote_verification_enabled: bool | None = None,
    remote_verification_public_origin: str | None = None,
    remote_verification_proof_secret: str | None = None,
) -> APIRouter:
    router = APIRouter()
    runtime_repository = SqliteRuntimeRepository(database_path)
    commands = SqliteCommandRepository(database_path)
    access = AccountAccessRepository(database_path)
    verification_create_handler = create_verification
    verification_get_handler = get_verification
    verification_frame_handler = get_verification_frame
    verification_input_handler = send_verification_input
    verification_complete_handler = complete_verification
    verification_cancel_handler = cancel_verification
    local_create_handler = create_local_verification
    local_handoff_handler = get_local_verification_handoff
    local_complete_handler = complete_local_verification
    local_handoff_enabled = (
        os.getenv("XIANYU_LOCAL_VERIFICATION_HANDOFF_ENABLED", "false").lower() == "true"
        if local_verification_enabled is None
        else local_verification_enabled
    )
    local_handoff_origin = _normalize_public_origin(
        local_verification_public_origin
        or os.getenv("XIANYU_LOCAL_VERIFICATION_PUBLIC_ORIGIN", "")
        or ""
    )
    remote_handoff_enabled = (
        os.getenv("XIANYU_REMOTE_VERIFICATION_ENABLED", "false").lower() == "true"
        if remote_verification_enabled is None
        else remote_verification_enabled
    )
    remote_handoff_origin = _normalize_public_origin(
        remote_verification_public_origin
        or os.getenv("XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN", "")
        or ""
    )
    remote_proof_signer = _build_remote_proof_signer(
        remote_verification_proof_secret
        or os.getenv("XIANYU_REMOTE_VERIFICATION_PROOF_SECRET", "")
        or os.getenv("XIANYU_CONNECTOR_INTERNAL_TOKEN", "")
        or ""
    )
    remote_registry = RemoteVerificationRegistry()

    def authorize(account_id: str, user: dict[str, Any]) -> None:
        is_admin = bool(user.get("is_admin") or user.get("username") == "admin")
        if not access.user_can_access(account_id, int(user["user_id"]), is_admin=is_admin):
            raise HTTPException(status_code=404, detail="account not found")

    def require_verification_handler(handler: Callable[..., Any] | None) -> Callable[..., Any]:
        if handler is None:
            raise HTTPException(status_code=503, detail="人工验证功能尚未启用")
        return handler

    def verification_cookie_name(session_id: str) -> str:
        return f"verification_ticket_{session_id}"

    def verification_ticket(request: Request, session_id: str) -> str | None:
        return request.cookies.get(verification_cookie_name(session_id))

    def verification_session_path(account_id: str, session_id: str) -> str:
        return (
            f"/api/accounts/{quote(account_id, safe='')}/verification-sessions/"
            f"{quote(session_id, safe='')}"
        )

    def remote_proof_cookie_name(session_id: str) -> str:
        return f"verification_remote_proof_{session_id}"

    def remote_viewer_path(account_id: str, session_id: str) -> str:
        return f"{verification_session_path(account_id, session_id)}/viewer"

    def remote_socket_path(account_id: str, session_id: str) -> str:
        return f"{verification_session_path(account_id, session_id)}/remote"

    def local_grant_cookie_name(session_id: str) -> str:
        return f"local_verification_grant_{session_id}"

    def local_session_path(account_id: str, session_id: str) -> str:
        return (
            f"/api/accounts/{quote(account_id, safe='')}/local-verification-sessions/"
            f"{quote(session_id, safe='')}"
        )

    def require_local_handoff() -> None:
        if not local_handoff_enabled or not local_handoff_origin:
            raise HTTPException(status_code=503, detail="本机验证功能尚未启用")

    def require_remote_verification() -> tuple[str, str, OperatorProofSigner, RemoteVerificationConnect]:
        if (
            not remote_handoff_enabled
            or not remote_handoff_origin
            or remote_proof_signer is None
            or connect_remote_verification is None
        ):
            raise HTTPException(status_code=503, detail="远程验证功能尚未启用")
        return (
            remote_handoff_origin[0],
            remote_handoff_origin[1],
            remote_proof_signer,
            connect_remote_verification,
        )

    def is_same_origin_remote_request(
        request: Request | WebSocket,
        *,
        destination: str,
        mode: str,
        origin_required: bool = True,
    ) -> bool:
        if not remote_handoff_origin:
            return False
        _, expected_host = remote_handoff_origin
        headers = request.headers
        supplied_origin = headers.get("origin", "").strip().lower()
        fetch_metadata = (
            headers.get("sec-fetch-site", "").strip().lower(),
            headers.get("sec-fetch-mode", "").strip().lower(),
            headers.get("sec-fetch-dest", "").strip().lower(),
        )
        fetch_metadata_valid = not any(fetch_metadata) or fetch_metadata == (
            "same-origin",
            mode,
            destination,
        )
        return (
            headers.get("host", "").strip().lower() == expected_host
            and (
                supplied_origin == remote_handoff_origin[0]
                or (not supplied_origin and not origin_required)
            )
            and fetch_metadata_valid
            and not request.query_params
        )

    def remote_proof_result(
        request: Request | WebSocket,
        account_id: str,
        session_id: str,
    ) -> tuple[ProofStatus, str | None]:
        if remote_proof_signer is None:
            return ProofStatus.INVALID, None
        token = request.cookies.get(remote_proof_cookie_name(session_id), "")
        result = remote_proof_signer.verify_bound(token, account_id, session_id)
        return result.status, result.user_id

    def mark_remote_no_store(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"

    def mark_remote_viewer_response(
        response: Response,
        *,
        remote_host: str | None = None,
    ) -> None:
        connect_source = (
            f"'self' wss://{remote_host}" if remote_host else "'none'"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            f"connect-src {connect_source}; img-src 'self' data:; "
            "frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
        )
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        mark_remote_no_store(response)

    def delete_remote_proof(response: Response, account_id: str, session_id: str) -> None:
        response.delete_cookie(
            remote_proof_cookie_name(session_id),
            path=verification_session_path(account_id, session_id),
            secure=True,
            httponly=True,
            samesite="strict",
        )

    def is_same_origin_navigation(request: Request) -> bool:
        if not local_handoff_origin:
            return False
        expected_origin, expected_host = local_handoff_origin
        request_host = request.headers.get("host", "").strip().lower()
        origin = request.headers.get("origin", "").strip().lower()
        fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
        fetch_mode = request.headers.get("sec-fetch-mode", "").strip().lower()
        fetch_destination = request.headers.get("sec-fetch-dest", "").strip().lower()
        fetch_metadata = (fetch_site, fetch_mode, fetch_destination)
        fetch_metadata_valid = not any(fetch_metadata) or fetch_metadata == (
            "same-origin",
            "navigate",
            "document",
        )
        return bool(
            request_host == expected_host
            and (not origin or origin == expected_origin)
            and fetch_metadata_valid
        )

    def mark_local_handoff_no_store(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"

    def delete_local_grant(response: Response, account_id: str, session_id: str) -> None:
        response.delete_cookie(
            local_grant_cookie_name(session_id),
            path=local_session_path(account_id, session_id),
            secure=True,
            httponly=True,
            samesite="strict",
        )

    def safe_verification_payload(payload: dict[str, Any]) -> dict[str, Any]:
        safe_payload = dict(payload)
        for secret_field in (
            "access_token",
            "ticket",
            "challenge_info",
            "challenge_url",
            "verification_url",
            "token",
        ):
            safe_payload.pop(secret_field, None)
        return safe_payload

    def mark_verification_no_store(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store, private"

    def verification_idempotency_key(
        account_id: str,
        current_user: dict[str, Any],
        provided_key: str | None,
    ) -> str:
        normalized_key = str(provided_key or "").strip()
        if normalized_key:
            return normalized_key
        runtime = runtime_repository.get(account_id) or AccountRuntime(account_id)
        material = "\0".join(
            (
                account_id,
                str(current_user["user_id"]),
                str(runtime.profile_generation),
                str(runtime.version),
            )
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return f"verification:{digest}"

    async def invoke_verification(
        handler: Callable[..., Awaitable[dict[str, Any]]],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            return await handler(*args, **kwargs)
        except ConnectorHttpError as exc:
            if exc.status_code == 401:
                raise HTTPException(
                    status_code=409,
                    detail="验证会话票据已失效，请重新发起人工验证",
                ) from exc
            messages = {
                404: "验证会话不存在或已过期",
                409: "当前验证会话不可用，请重新发起人工验证",
            }
            status_code = exc.status_code if exc.status_code in messages else 503
            raise HTTPException(
                status_code=status_code,
                detail=messages.get(status_code, "连接器暂时不可用，请稍后重试"),
            ) from exc

    @router.get("/api/accounts/{account_id}/runtime")
    async def get_runtime(
        account_id: str,
        current_user: dict[str, Any] = Depends(current_user_dependency),
    ) -> dict[str, Any]:
        authorize(account_id, current_user)
        runtime = runtime_repository.get(account_id)
        return _runtime_payload(runtime or AccountRuntime(account_id))

    @router.post("/api/accounts/{account_id}/commands", status_code=202)
    async def submit_command(
        account_id: str,
        request: CommandRequest,
        idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
        current_user: dict[str, Any] = Depends(current_user_dependency),
    ) -> dict[str, str]:
        authorize(account_id, current_user)
        if request.command is AccountCommand.RELOGIN_QR:
            raise HTTPException(
                status_code=409,
                detail="二维码重登必须通过 /qr-sessions 发起",
            )
        command = commands.enqueue(
            account_id,
            request.command,
            idempotency_key,
            request.payload,
        )
        return {"command_id": command.command_id, "status": command.status.value}

    @router.post("/api/accounts/{account_id}/qr-sessions", status_code=202)
    async def create_qr_session(
        account_id: str,
        current_user: dict[str, Any] = Depends(current_user_dependency),
    ) -> dict[str, Any]:
        authorize(account_id, current_user)
        return await generate_qr(current_user, account_id)

    @router.get("/api/accounts/{account_id}/qr-sessions/{session_id}")
    async def get_qr_session(
        account_id: str,
        session_id: str,
        current_user: dict[str, Any] = Depends(current_user_dependency),
    ) -> dict[str, Any]:
        authorize(account_id, current_user)
        return await check_qr(session_id, current_user, account_id)

    @router.post("/api/accounts/{account_id}/verification-sessions", status_code=202)
    async def create_verification_session(
        account_id: str,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        current_user: dict[str, Any] = Depends(current_user_dependency),
    ) -> dict[str, Any]:
        authorize(account_id, current_user)
        mark_verification_no_store(response)
        handler = require_verification_handler(verification_create_handler)
        request_key = verification_idempotency_key(
            account_id,
            current_user,
            idempotency_key,
        )
        payload = dict(
            await invoke_verification(handler, current_user, account_id, request_key)
        )
        session_id = str(payload.get("session_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
            raise HTTPException(status_code=502, detail="连接器未返回验证会话")
        ticket = payload.pop("access_token", None) or payload.pop("ticket", None)
        for secret_field in ("challenge_info", "challenge_url", "verification_url", "token"):
            payload.pop(secret_field, None)
        if ticket:
            response.set_cookie(
                verification_cookie_name(session_id),
                str(ticket),
                max_age=600,
                httponly=True,
                secure=True,
                samesite="strict",
                path=f"/api/accounts/{account_id}/verification-sessions/{session_id}",
            )
        payload["ticket_required"] = bool(ticket)
        return payload

    @router.get("/api/accounts/{account_id}/verification-sessions/{session_id}")
    async def get_verification_session_route(
        account_id: str,
        session_id: str,
        request: Request,
        response: Response,
        after_seq: int = 0,
        current_user: dict[str, Any] = Depends(current_user_dependency),
    ) -> dict[str, Any]:
        authorize(account_id, current_user)
        mark_verification_no_store(response)
        handler = require_verification_handler(verification_get_handler)
        payload = await invoke_verification(
            handler,
            session_id,
            current_user,
            account_id,
            ticket=verification_ticket(request, session_id),
            after_seq=max(0, after_seq),
        )
        if _is_terminal_remote_session_state(payload):
            delete_remote_proof(response, account_id, session_id)
        return safe_verification_payload(payload)

    @router.get("/api/accounts/{account_id}/verification-sessions/{session_id}/frame")
    async def get_verification_frame_route(
        account_id: str,
        session_id: str,
        request: Request,
        response: Response,
        after_seq: int = 0,
        current_user: dict[str, Any] = Depends(current_user_dependency),
    ) -> dict[str, Any]:
        authorize(account_id, current_user)
        mark_verification_no_store(response)
        handler = require_verification_handler(verification_frame_handler)
        return safe_verification_payload(
            await invoke_verification(
                handler,
                session_id,
                current_user,
                account_id,
                ticket=verification_ticket(request, session_id),
                after_seq=max(0, after_seq),
            )
        )

    @router.post("/api/accounts/{account_id}/verification-sessions/{session_id}/input")
    async def send_verification_input_route(
        account_id: str,
        session_id: str,
        request_data: VerificationInputRequest,
        request: Request,
        response: Response,
        current_user: dict[str, Any] = Depends(current_user_dependency),
    ) -> dict[str, Any]:
        authorize(account_id, current_user)
        mark_verification_no_store(response)
        handler = require_verification_handler(verification_input_handler)
        input_payload = request_data.model_dump(exclude_none=True)
        input_payload["action"] = input_payload.pop("type")
        return safe_verification_payload(
            await invoke_verification(
                handler,
                session_id,
                current_user,
                account_id,
                input_payload,
                ticket=verification_ticket(request, session_id),
            )
        )

    @router.post("/api/accounts/{account_id}/verification-sessions/{session_id}/complete")
    async def complete_verification_session(
        account_id: str,
        session_id: str,
        request: Request,
        response: Response,
        request_data: VerificationCompleteRequest | None = None,
        current_user: dict[str, Any] = Depends(current_user_dependency),
    ) -> dict[str, Any]:
        authorize(account_id, current_user)
        mark_verification_no_store(response)
        handler = require_verification_handler(verification_complete_handler)
        result = await invoke_verification(
            handler,
            session_id,
            current_user,
            account_id,
            (request_data or VerificationCompleteRequest()).model_dump(exclude_none=True),
            ticket=verification_ticket(request, session_id),
        )
        if _is_terminal_remote_session_state(result):
            delete_remote_proof(response, account_id, session_id)
        # Keep the short-lived HttpOnly ticket while the control plane polls the
        # connector's VERIFYING state. It is cleared on cancellation or expiry.
        return safe_verification_payload(result)

    @router.delete("/api/accounts/{account_id}/verification-sessions/{session_id}")
    async def cancel_verification_session(
        account_id: str,
        session_id: str,
        request: Request,
        response: Response,
        current_user: dict[str, Any] = Depends(current_user_dependency),
    ) -> dict[str, Any]:
        authorize(account_id, current_user)
        mark_verification_no_store(response)
        handler = require_verification_handler(verification_cancel_handler)
        result = await invoke_verification(
            handler,
            session_id,
            current_user,
            account_id,
            ticket=verification_ticket(request, session_id),
        )
        response.delete_cookie(
            verification_cookie_name(session_id),
            path=f"/api/accounts/{account_id}/verification-sessions/{session_id}",
        )
        delete_remote_proof(response, account_id, session_id)
        return safe_verification_payload(result)

    @router.post(
        "/api/accounts/{account_id}/verification-sessions/{session_id}/remote-proof",
        status_code=201,
    )
    async def mint_remote_verification_proof(
        account_id: str,
        session_id: str,
        request: Request,
        response: Response,
        request_data: RemoteVerificationProofRequest | None = None,
        current_user: dict[str, Any] = Depends(current_user_dependency),
    ) -> dict[str, str]:
        del request_data
        _, _, signer, _ = require_remote_verification()
        authorize(account_id, current_user)
        handler = require_verification_handler(verification_get_handler)
        payload = await invoke_verification(
            handler,
            session_id,
            current_user,
            account_id,
            ticket=verification_ticket(request, session_id),
            after_seq=0,
        )
        _require_remote_session_state(payload)
        if await remote_registry.occupied(account_id, session_id):
            raise HTTPException(status_code=409, detail="远程验证窗口已被占用")
        # The proof is a short-lived reconnect lease, not a one-time token. Every
        # viewer/WS request revalidates its bound operator, account, session and
        # current session state before the connector bridge is opened.
        proof = signer.issue(current_user["user_id"], account_id, session_id)
        response.set_cookie(
            remote_proof_cookie_name(session_id),
            proof,
            max_age=signer.ttl_seconds,
            httponly=True,
            secure=True,
            samesite="strict",
            path=verification_session_path(account_id, session_id),
        )
        mark_remote_no_store(response)
        return {
            "viewer_url": remote_viewer_path(account_id, session_id),
            "websocket_url": remote_socket_path(account_id, session_id),
        }

    @router.delete(
        "/api/accounts/{account_id}/verification-sessions/{session_id}/remote-proof",
        status_code=204,
    )
    async def revoke_remote_verification_proof(
        account_id: str,
        session_id: str,
        response: Response,
        current_user: dict[str, Any] = Depends(current_user_dependency),
    ) -> Response:
        authorize(account_id, current_user)
        delete_remote_proof(response, account_id, session_id)
        mark_remote_no_store(response)
        response.status_code = 204
        return response

    @router.get(
        "/api/accounts/{account_id}/verification-sessions/{session_id}/viewer",
        response_class=HTMLResponse,
    )
    async def remote_verification_viewer(
        account_id: str,
        session_id: str,
        request: Request,
    ) -> Response:
        try:
            _, remote_host, _, _ = require_remote_verification()
        except HTTPException as exc:
            response = Response(status_code=exc.status_code)
            mark_remote_viewer_response(response)
            return response
        if not is_same_origin_remote_request(
            request,
            destination="iframe",
            mode="navigate",
            origin_required=False,
        ):
            response = Response(status_code=403)
            mark_remote_viewer_response(response)
            return response
        proof_status, proof_user_id = remote_proof_result(request, account_id, session_id)
        if proof_status is ProofStatus.EXPIRED:
            response = Response(status_code=410)
            delete_remote_proof(response, account_id, session_id)
            mark_remote_viewer_response(response)
            return response
        if proof_status is not ProofStatus.VALID or proof_user_id is None:
            response = Response(status_code=403)
            mark_remote_viewer_response(response)
            return response
        try:
            handler = require_verification_handler(verification_get_handler)
            payload = await invoke_verification(
                handler,
                session_id,
                {"user_id": proof_user_id},
                account_id,
                ticket=verification_ticket(request, session_id),
                after_seq=0,
            )
            _require_remote_session_state(payload)
        except HTTPException as exc:
            response = Response(status_code=exc.status_code)
            if exc.status_code == 410:
                delete_remote_proof(response, account_id, session_id)
            mark_remote_viewer_response(response)
            return response
        if await remote_registry.occupied(account_id, session_id):
            response = Response(status_code=409)
            mark_remote_viewer_response(response)
            return response
        response = HTMLResponse(REMOTE_VIEWER_HTML)
        mark_remote_viewer_response(response, remote_host=remote_host)
        return response

    @router.websocket(
        "/api/accounts/{account_id}/verification-sessions/{session_id}/remote"
    )
    async def remote_verification_socket(
        account_id: str,
        session_id: str,
        websocket: WebSocket,
    ) -> None:
        try:
            _, _, _, connect_handler = require_remote_verification()
        except HTTPException:
            await websocket.close(code=1013)
            return
        if not is_same_origin_remote_request(
            websocket,
            destination="empty",
            mode="websocket",
        ):
            await websocket.close(code=1008)
            return
        proof_status, proof_user_id = remote_proof_result(websocket, account_id, session_id)
        if proof_status is ProofStatus.EXPIRED:
            await websocket.close(code=4401)
            return
        if proof_status is not ProofStatus.VALID or proof_user_id is None:
            await websocket.close(code=1008)
            return
        ticket = websocket.cookies.get(verification_cookie_name(session_id), "")
        if not ticket:
            await websocket.close(code=1008)
            return
        try:
            handler = require_verification_handler(verification_get_handler)
            payload = await invoke_verification(
                handler,
                session_id,
                {"user_id": proof_user_id},
                account_id,
                ticket=ticket,
                after_seq=0,
            )
            _require_remote_session_state(payload)
        except HTTPException:
            await websocket.close(code=1008)
            return
        if not await remote_registry.acquire(account_id, session_id):
            await websocket.close(code=1008)
            return
        try:
            async with connect_handler(
                account_id,
                session_id,
                ticket=ticket,
                operator_id=proof_user_id,
            ) as connector:
                await bridge_remote_verification(websocket, connector)
        except asyncio.CancelledError:
            pass
        except Exception:
            with suppress(RuntimeError):
                await websocket.close(code=1011)
        finally:
            await remote_registry.release(account_id, session_id)

    @router.post(
        "/api/accounts/{account_id}/local-verification-sessions",
        status_code=201,
    )
    async def create_local_verification_session(
        account_id: str,
        response: Response,
        idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
        current_user: dict[str, Any] = Depends(current_user_dependency),
    ) -> dict[str, str]:
        require_local_handoff()
        authorize(account_id, current_user)
        handler = require_verification_handler(local_create_handler)
        payload = dict(
            await invoke_verification(
                handler,
                current_user,
                account_id,
                idempotency_key,
            )
        )
        session_id = str(payload.get("session_id") or "").strip()
        grant = str(payload.get("handoff_token") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id) or not grant:
            raise HTTPException(status_code=502, detail="连接器未返回本机验证授权")
        session_path = local_session_path(account_id, session_id)
        response.set_cookie(
            local_grant_cookie_name(session_id),
            grant,
            max_age=LOCAL_HANDOFF_GRANT_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="strict",
            path=session_path,
        )
        mark_local_handoff_no_store(response)
        return {"handoff_url": f"{session_path}/handoff"}

    @router.get("/api/accounts/{account_id}/local-verification-sessions/{session_id}/handoff")
    async def open_local_verification_handoff(
        account_id: str,
        session_id: str,
        request: Request,
    ) -> Response:
        require_local_handoff()
        if not is_same_origin_navigation(request):
            response = Response(status_code=403)
            mark_local_handoff_no_store(response)
            return response
        grant = request.cookies.get(local_grant_cookie_name(session_id), "")
        if not grant:
            response = Response(status_code=410)
            mark_local_handoff_no_store(response)
            delete_local_grant(response, account_id, session_id)
            return response
        handler = require_verification_handler(local_handoff_handler)
        try:
            location = await handler(session_id, account_id, ticket=grant)
        except ConnectorHttpError as exc:
            status_code = exc.status_code if exc.status_code == 410 else 503
            response = Response(status_code=status_code)
            mark_local_handoff_no_store(response)
            if exc.status_code == 410:
                delete_local_grant(response, account_id, session_id)
            return response
        response = Response(status_code=302 if _is_safe_platform_redirect(location) else 502)
        mark_local_handoff_no_store(response)
        delete_local_grant(response, account_id, session_id)
        if response.status_code == 302:
            response.headers["Location"] = location
        return response

    @router.post(
        "/api/accounts/{account_id}/local-verification-sessions/{session_id}/complete",
        status_code=202,
    )
    async def complete_local_verification_session(
        account_id: str,
        session_id: str,
        response: Response,
        request_data: LocalVerificationCompleteRequest | None = None,
        idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
        current_user: dict[str, Any] = Depends(current_user_dependency),
    ) -> dict[str, Any]:
        del request_data, idempotency_key
        require_local_handoff()
        authorize(account_id, current_user)
        handler = require_verification_handler(local_complete_handler)
        result = await invoke_verification(handler, session_id, current_user, account_id)
        mark_local_handoff_no_store(response)
        return safe_verification_payload(result)

    @router.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "healthy"}

    @router.get("/health/ready")
    async def health_ready(response: Response) -> dict[str, object]:
        online = [
            runtime.account_id
            for runtime in runtime_repository.list_all()
            if runtime.state is AccountState.ONLINE and runtime.readiness.online
        ]
        if not online:
            response.status_code = 503
        return {"status": "ready" if online else "not_ready", "online_accounts": online}

    return router


def _runtime_payload(runtime: AccountRuntime) -> dict[str, Any]:
    return {
        "account_id": runtime.account_id,
        "state": runtime.state.value,
        "version": runtime.version,
        "readiness": {
            "session": runtime.readiness.session_ready,
            "token": runtime.readiness.token_ready,
            "websocket": runtime.readiness.websocket_ready,
            "stream": runtime.readiness.stream_ready,
            "online": runtime.readiness.online,
        },
        "reason_code": runtime.reason_code,
        "reason_message": runtime.reason_message,
        "entered_at": runtime.entered_at.isoformat(),
        "last_heartbeat_ack_at": _format_time(runtime.last_heartbeat_ack_at),
        "last_session_keepalive_at": _format_time(runtime.last_session_keepalive_at),
        "last_business_message_at": _format_time(runtime.last_business_message_at),
        "next_action_at": _format_time(runtime.next_action_at),
        "worker_pid": runtime.worker_pid,
        "restart_count": runtime.restart_count,
    }


def _build_remote_proof_signer(secret: str) -> OperatorProofSigner | None:
    if not secret:
        return None
    try:
        return OperatorProofSigner(secret, ttl_seconds=REMOTE_VERIFICATION_PROOF_TTL_SECONDS)
    except ValueError:
        return None


def _require_remote_session_state(payload: dict[str, Any]) -> None:
    state = str(payload.get("state") or "").strip().lower()
    if state in {"expired", "cancelled"}:
        raise HTTPException(status_code=410, detail="验证会话已过期")
    if state in {"succeeded", "failed", "new_challenge", "manual_device_required"}:
        raise HTTPException(status_code=409, detail="验证会话当前不可打开远程窗口")


def _is_terminal_remote_session_state(payload: dict[str, Any]) -> bool:
    state = str(payload.get("state") or "").strip().lower()
    return state in {
        "expired",
        "cancelled",
        "succeeded",
        "failed",
        "new_challenge",
        "manual_device_required",
    }


def _is_safe_platform_redirect(value: str) -> bool:
    if not value or len(value) > 8192 or "\r" in value or "\n" in value:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed_domains = ("goofish.com", "taobao.com", "tmall.com")
    return bool(
        parsed.scheme == "https"
        and port in (None, 443)
        and not parsed.username
        and not parsed.password
        and any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)
    )


def _normalize_public_origin(value: str) -> tuple[str, str] | None:
    candidate = value.strip().rstrip("/")
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or port not in (None, 443)
    ):
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    expected_host = host if port is None else f"{host}:{port}"
    return f"https://{expected_host}", expected_host


def _format_time(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
