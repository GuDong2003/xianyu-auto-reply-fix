from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Protocol

from starlette.websockets import WebSocket, WebSocketDisconnect


class RemoteVerificationSocket(Protocol):
    async def recv(self) -> str | bytes: ...

    async def send(self, data: str | bytes) -> None: ...


class RemoteVerificationRegistry:
    def __init__(self) -> None:
        self._active: set[tuple[str, str]] = set()
        self._lock = asyncio.Lock()

    async def occupied(self, account_id: str, session_id: str) -> bool:
        async with self._lock:
            return (account_id, session_id) in self._active

    async def acquire(self, account_id: str, session_id: str) -> bool:
        key = (account_id, session_id)
        async with self._lock:
            if key in self._active:
                return False
            self._active.add(key)
            return True

    async def release(self, account_id: str, session_id: str) -> None:
        async with self._lock:
            self._active.discard((account_id, session_id))


async def bridge_remote_verification(
    browser: WebSocket,
    connector: RemoteVerificationSocket,
) -> None:
    await browser.accept()
    tasks = {
        asyncio.create_task(_browser_to_connector(browser, connector)),
        asyncio.create_task(_connector_to_browser(connector, browser)),
    }
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        done, pending = set(), tasks
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        with suppress(asyncio.CancelledError, WebSocketDisconnect):
            task.result()


async def _browser_to_connector(
    browser: WebSocket,
    connector: RemoteVerificationSocket,
) -> None:
    while True:
        message = await browser.receive()
        if message["type"] == "websocket.disconnect":
            return
        data = message.get("bytes")
        if data is None:
            data = message.get("text")
        if data is not None:
            await connector.send(data)


async def _connector_to_browser(
    connector: RemoteVerificationSocket,
    browser: WebSocket,
) -> None:
    while True:
        data = await connector.recv()
        if isinstance(data, bytes):
            await browser.send_bytes(data)
        else:
            await browser.send_text(data)
