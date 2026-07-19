from __future__ import annotations

from typing import Any, ClassVar

import httpx
import pytest
import websockets

from xianyu_control.connector_client import ConnectorClient


class FakeAsyncClient:
    init_options: ClassVar[dict[str, Any]] = {}
    requests: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **options: Any) -> None:
        type(self).init_options = options

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def request(self, method: str, url: str, **options: Any) -> httpx.Response:
        type(self).requests.append({"method": method, "url": url, **options})
        return httpx.Response(
            302,
            headers={"Location": "https://challenge.goofish.com/verify"},
            request=httpx.Request(method, url),
        )


@pytest.mark.asyncio
async def test_verification_create_requires_server_derived_idempotency_key() -> None:
    client = ConnectorClient("http://connector:8091", "internal-secret")

    with pytest.raises(ValueError, match="idempotency"):
        await client.create_verification_session({"user_id": 7}, "account-1")


@pytest.mark.asyncio
async def test_handoff_client_does_not_follow_redirect_or_leak_ticket(monkeypatch) -> None:
    FakeAsyncClient.requests = []
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = ConnectorClient("http://connector:8091", "internal-secret")

    location = await client.get_local_verification_handoff(
        "local-1",
        "account-1",
        ticket="handoff-grant",
    )

    assert location == "https://challenge.goofish.com/verify"
    assert FakeAsyncClient.init_options["follow_redirects"] is False
    request = FakeAsyncClient.requests[0]
    assert request["method"] == "GET"
    assert request["url"].endswith(
        "/internal/accounts/account-1/local-verification-sessions/local-1/handoff"
    )
    assert request["headers"]["X-Verification-Ticket"] == "handoff-grant"
    assert "handoff-grant" not in request["url"]
    assert request.get("json") is None


@pytest.mark.asyncio
async def test_local_create_only_sends_trusted_operator_identity(monkeypatch) -> None:
    class CreateAsyncClient(FakeAsyncClient):
        async def request(self, method: str, url: str, **options: Any) -> httpx.Response:
            type(self).requests.append({"method": method, "url": url, **options})
            return httpx.Response(
                201,
                json={
                    "session_id": "local-1",
                    "state": "waiting_for_operator",
                    "handoff_token": "grant",
                },
                request=httpx.Request(method, url),
            )

    CreateAsyncClient.requests = []
    monkeypatch.setattr(httpx, "AsyncClient", CreateAsyncClient)
    client = ConnectorClient("http://connector:8091", "internal-secret")

    await client.create_local_verification_session(
        {"user_id": 7},
        "account-1",
        "fresh-attempt-id",
    )

    request = CreateAsyncClient.requests[0]
    assert request["json"] == {
        "user_id": 7,
        "idempotency_key": "fresh-attempt-id",
    }
    assert request["url"].endswith(
        "/internal/accounts/account-1/local-verification-sessions"
    )


@pytest.mark.asyncio
async def test_local_complete_sends_empty_body_and_trusted_operator_header(monkeypatch) -> None:
    class CompleteAsyncClient(FakeAsyncClient):
        async def request(self, method: str, url: str, **options: Any) -> httpx.Response:
            type(self).requests.append({"method": method, "url": url, **options})
            return httpx.Response(
                202,
                json={"session_id": "local-1", "state": "verifying"},
                request=httpx.Request(method, url),
            )

    CompleteAsyncClient.requests = []
    monkeypatch.setattr(httpx, "AsyncClient", CompleteAsyncClient)
    client = ConnectorClient("http://connector:8091", "internal-secret")

    await client.complete_local_verification_session(
        "local-1",
        {"user_id": 7},
        "account-1",
    )

    request = CompleteAsyncClient.requests[0]
    assert request["json"] == {}
    assert request["headers"]["X-Operator-Id"] == "7"
    assert "X-Verification-Ticket" not in request["headers"]


def test_remote_websocket_uses_internal_headers_without_query_credentials(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_connect(url: str, **options: Any) -> object:
        captured.update({"url": url, **options})
        return object()

    monkeypatch.setattr(websockets, "connect", fake_connect)
    client = ConnectorClient("http://connector:8091", "internal-secret")

    result = client.connect_remote_verification(
        "account-1",
        "verify-1",
        ticket="verification-ticket",
        operator_id="7",
    )

    assert result is not None
    assert captured["url"] == (
        "ws://connector:8091/internal/accounts/account-1/verification-sessions/verify-1/rfb"
    )
    assert captured["extra_headers"]["X-Connector-Token"] == "internal-secret"
    assert captured["extra_headers"]["X-Verification-Ticket"] == "verification-ticket"
    assert captured["extra_headers"]["X-Operator-Id"] == "7"
    assert "internal-secret" not in captured["url"]
    assert "verification-ticket" not in captured["url"]
    assert "?" not in captured["url"]
