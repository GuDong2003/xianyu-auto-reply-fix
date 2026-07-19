from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
import websockets


class ConnectorHttpError(RuntimeError):
    """Safe connector failure that can be translated by the control router."""

    def __init__(self, status_code: int) -> None:
        self.status_code = int(status_code)
        super().__init__(f"connector request failed with HTTP {self.status_code}")


class ConnectorClient:
    def __init__(self, base_url: str, token: str, *, timeout_seconds: float = 35) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Connector-Token": token}
        self._timeout = timeout_seconds

    async def create_qr_session(
        self,
        current_user: Mapping[str, Any],
        account_id: str | None,
    ) -> dict[str, Any]:
        if not account_id:
            raise ValueError("external connector requires a target account")
        return await self._request(
            "POST",
            f"/internal/accounts/{account_id}/qr-sessions",
            json_payload={"user_id": int(current_user["user_id"])},
        )

    async def get_qr_session(
        self,
        session_id: str,
        current_user: Mapping[str, Any],
        account_id: str,
    ) -> dict[str, Any]:
        del current_user
        return await self._request(
            "GET",
            f"/internal/accounts/{account_id}/qr-sessions/{session_id}",
        )

    async def create_verification_session(
        self,
        current_user: Mapping[str, Any],
        account_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Start a protected, operator-controlled browser verification session."""
        request_key = str(idempotency_key or "").strip()
        if not request_key:
            raise ValueError("verification idempotency key is required")
        return await self._request(
            "POST",
            f"/internal/accounts/{account_id}/verification-sessions",
            json_payload={
                "user_id": int(current_user["user_id"]),
                "idempotency_key": request_key,
            },
        )

    async def get_verification_session(
        self,
        session_id: str,
        current_user: Mapping[str, Any],
        account_id: str,
        *,
        ticket: str | None = None,
        after_seq: int = 0,
    ) -> dict[str, Any]:
        del current_user
        return await self._request(
            "GET",
            f"/internal/accounts/{account_id}/verification-sessions/{session_id}",
            query={"after_seq": max(0, int(after_seq))},
            verification_ticket=ticket,
        )

    async def get_verification_frame(
        self,
        session_id: str,
        current_user: Mapping[str, Any],
        account_id: str,
        *,
        ticket: str | None = None,
        after_seq: int = 0,
    ) -> dict[str, Any]:
        del current_user
        return await self._request(
            "GET",
            f"/internal/accounts/{account_id}/verification-sessions/{session_id}/frame",
            query={"after_seq": max(0, int(after_seq))},
            verification_ticket=ticket,
        )

    async def send_verification_input(
        self,
        session_id: str,
        current_user: Mapping[str, Any],
        account_id: str,
        payload: dict[str, Any],
        *,
        ticket: str | None = None,
    ) -> dict[str, Any]:
        del current_user
        return await self._request(
            "POST",
            f"/internal/accounts/{account_id}/verification-sessions/{session_id}/input",
            json_payload=payload,
            verification_ticket=ticket,
        )

    async def complete_verification_session(
        self,
        session_id: str,
        current_user: Mapping[str, Any],
        account_id: str,
        payload: dict[str, Any],
        *,
        ticket: str | None = None,
    ) -> dict[str, Any]:
        del current_user
        return await self._request(
            "POST",
            f"/internal/accounts/{account_id}/verification-sessions/{session_id}/complete",
            json_payload=payload,
            verification_ticket=ticket,
        )

    async def cancel_verification_session(
        self,
        session_id: str,
        current_user: Mapping[str, Any],
        account_id: str,
        *,
        ticket: str | None = None,
    ) -> dict[str, Any]:
        del current_user
        return await self._request(
            "DELETE",
            f"/internal/accounts/{account_id}/verification-sessions/{session_id}",
            verification_ticket=ticket,
        )

    async def create_local_verification_session(
        self,
        current_user: Mapping[str, Any],
        account_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        operator_id = int(current_user["user_id"])
        return await self._request(
            "POST",
            f"/internal/accounts/{account_id}/local-verification-sessions",
            json_payload={
                "user_id": operator_id,
                "idempotency_key": idempotency_key,
            },
        )

    async def get_local_verification_handoff(
        self,
        session_id: str,
        account_id: str,
        *,
        ticket: str,
    ) -> str:
        headers = {**self._headers, "X-Verification-Ticket": ticket}
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
        ) as client:
            response = await client.request(
                "GET",
                f"{self._base_url}/internal/accounts/{account_id}/local-verification-sessions/{session_id}/handoff",
                headers=headers,
            )
        if response.status_code != 302:
            raise ConnectorHttpError(response.status_code)
        location = str(response.headers.get("location") or "").strip()
        if not location:
            raise ConnectorHttpError(502)
        return location

    async def complete_local_verification_session(
        self,
        session_id: str,
        current_user: Mapping[str, Any],
        account_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/internal/accounts/{account_id}/local-verification-sessions/{session_id}/complete",
            json_payload={},
            operator_id=int(current_user["user_id"]),
        )

    def connect_remote_verification(
        self,
        account_id: str,
        session_id: str,
        *,
        ticket: str,
        operator_id: str,
    ) -> Any:
        headers = {
            **self._headers,
            "X-Operator-Id": operator_id,
            "X-Verification-Ticket": ticket,
        }
        path = (
            f"/internal/accounts/{quote(account_id, safe='')}/verification-sessions/"
            f"{quote(session_id, safe='')}/rfb"
        )
        return websockets.connect(
            _websocket_url(self._base_url, path),
            extra_headers=headers,
            open_timeout=self._timeout,
            compression=None,
            max_size=None,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        verification_ticket: str | None = None,
        operator_id: int | None = None,
    ) -> dict[str, Any]:
        headers = dict(self._headers)
        if verification_ticket:
            headers["X-Verification-Ticket"] = verification_ticket
        if operator_id is not None:
            headers["X-Operator-Id"] = str(operator_id)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                json=json_payload,
                params=query,
            )
        if response.is_error:
            raise ConnectorHttpError(response.status_code)
        if response.status_code == 204:
            return {}
        return dict(response.json())


def _websocket_url(base_url: str, path: str) -> str:
    parsed = urlsplit(base_url)
    schemes = {"http": "ws", "https": "wss"}
    if parsed.scheme not in schemes or not parsed.netloc:
        raise ValueError("connector base URL must be HTTP(S)")
    return urlunsplit((schemes[parsed.scheme], parsed.netloc, path, "", ""))
