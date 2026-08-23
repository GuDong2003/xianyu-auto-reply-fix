from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, Protocol

from xianyu_connector.infrastructure.verification_process import (
    RfbConnectionLease,
    RfbEndpoint,
)


class BinaryWebSocket(Protocol):
    async def accept(self) -> None: ...

    async def receive(self) -> Any: ...

    async def send_bytes(self, data: bytes) -> None: ...

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


OpenConnection = Callable[
    [str, int],
    Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]],
]


class RfbWebSocketBridge:
    def __init__(
        self,
        *,
        open_connection: OpenConnection = asyncio.open_connection,
        connect_timeout_seconds: float = 3.0,
    ) -> None:
        self._open_connection = open_connection
        self._connect_timeout_seconds = connect_timeout_seconds

    async def relay(self, websocket: BinaryWebSocket, lease: RfbConnectionLease) -> None:
        await websocket.accept()
        writer: asyncio.StreamWriter | None = None
        try:
            reader, connected_writer = await asyncio.wait_for(
                self._connect(lease.endpoint),
                timeout=self._connect_timeout_seconds,
            )
            writer = connected_writer
            await self._relay_bidirectionally(websocket, reader, connected_writer, lease)
        except (OSError, TimeoutError):
            with suppress(RuntimeError):
                await websocket.close(code=1011, reason="RFB server unavailable")
        finally:
            if writer is not None:
                writer.close()
                with suppress(OSError, RuntimeError):
                    await writer.wait_closed()
            lease.close()

    async def _connect(
        self,
        endpoint: RfbEndpoint,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        last_error: OSError | None = None
        for delay in (0.0, 0.05, 0.1, 0.2, 0.4):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await self._open_connection(endpoint.host, endpoint.port)
            except OSError as exc:
                last_error = exc
        if last_error is None:
            raise OSError("RFB server unavailable")
        raise last_error

    async def _relay_bidirectionally(
        self,
        websocket: BinaryWebSocket,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        lease: RfbConnectionLease,
    ) -> None:
        tasks = {
            asyncio.create_task(self._websocket_to_tcp(websocket, writer, lease)),
            asyncio.create_task(self._tcp_to_websocket(reader, websocket)),
        }
        _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _websocket_to_tcp(
        websocket: BinaryWebSocket,
        writer: asyncio.StreamWriter,
        lease: RfbConnectionLease,
    ) -> None:
        while True:
            message = await websocket.receive()
            message_type = str(message.get("type") or "")
            if message_type == "websocket.disconnect":
                return
            data = message.get("bytes")
            if not isinstance(data, bytes):
                await websocket.close(code=1003, reason="binary RFB frames required")
                return
            lease.touch()
            writer.write(data)
            await writer.drain()

    @staticmethod
    async def _tcp_to_websocket(
        reader: asyncio.StreamReader,
        websocket: BinaryWebSocket,
    ) -> None:
        while data := await reader.read(65_536):
            await websocket.send_bytes(data)
