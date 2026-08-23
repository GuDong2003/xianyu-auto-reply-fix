from __future__ import annotations

import asyncio
import os
import signal
from contextlib import suppress


async def drain_stream(stream: asyncio.StreamReader | None) -> None:
    if stream is None:
        return
    while await stream.readline():
        pass


async def terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        await process.wait()
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
