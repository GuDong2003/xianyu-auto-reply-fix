from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from pathlib import Path

from xianyu_connector.application.account_operation_coordinator import (
    AccountOperationConflict,
    AccountOperationCoordinator,
    AccountOperationKind,
)
from xianyu_connector.application.qr_auth_session import (
    QrAuthSession,
    QrAuthSessionLifecycle,
)
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.domain.commands import AccountCommand, CommandStatus
from xianyu_connector.infrastructure.async_process import drain_stream, terminate_process
from xianyu_connector.infrastructure.sqlite_command_repository import SqliteCommandRepository
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository

ONLINE_READINESS_TIMEOUT_SECONDS = 90.0
ACCOUNT_STOP_TIMEOUT_SECONDS = 20.0


class QrAuthManager(QrAuthSessionLifecycle):
    def __init__(
        self,
        database_path: Path,
        profiles_root: Path,
        master_key_path: Path,
        command_repository: SqliteCommandRepository,
        runtime_repository: SqliteRuntimeRepository,
        *,
        qr_generation_timeout_seconds: float = 30,
        stop_verification: Callable[[str], Awaitable[None] | None] | None = None,
        coordinator: AccountOperationCoordinator | None = None,
    ) -> None:
        self._database_path = database_path
        self._profiles_root = profiles_root
        self._master_key_path = master_key_path
        self._commands = command_repository
        self._runtimes = runtime_repository
        self._qr_generation_timeout_seconds = qr_generation_timeout_seconds
        self._stop_verification = stop_verification
        self._coordinator = coordinator or AccountOperationCoordinator()
        self._sessions: dict[str, QrAuthSession] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._recover_orphaned_qr_states()

    async def create(self, account_id: str, user_id: int) -> dict[str, object]:
        async with self._coordinator.hold_async(account_id):
            active = self._active_for_account(account_id)
            if active:
                await self._await_qr_or_fail(active)
                return self.payload(active)
            session = QrAuthSession(uuid.uuid4().hex, account_id, user_id)
            self._sessions[session.session_id] = session
            if not await self._stop_active_verification(session):
                return self.payload(session)
            try:
                session.operation_lease = self._coordinator.reserve(
                    account_id,
                    AccountOperationKind.QR,
                )
                stop_command = self._commands.enqueue(
                    account_id,
                    AccountCommand.RELOGIN_QR,
                    f"qr-stop:{session.session_id}",
                    {},
                )
            except AccountOperationConflict:
                session.state = "error"
                session.message = "账号正在执行其他操作，请稍后重试"
                self._release_operation(session)
                return self.payload(session)
            session.stop_command_id = stop_command.command_id
            self._tasks[session.session_id] = asyncio.create_task(self._run(session))
        await self._await_qr_or_fail(session)
        return self.payload(session)

    async def _stop_active_verification(self, session: QrAuthSession) -> bool:
        if self._stop_verification is None:
            return True
        try:
            result = self._stop_verification(session.account_id)
            if result is not None:
                await result
        except Exception:
            session.state = "error"
            session.message = "验证浏览器停止失败，请重新尝试"
            return False
        return True

    def get(self, session_id: str, account_id: str) -> dict[str, object] | None:
        session = self._sessions.get(session_id)
        if not session or session.account_id != account_id:
            return None
        self._refresh_online_state(session)
        return self.payload(session)

    def payload(self, session: QrAuthSession) -> dict[str, object]:
        status = _public_status(session.state)
        expires_from = session.qr_started_at or session.started_at
        return {
            "success": bool(session.qr_code_url) and status not in {"error", "expired"},
            "session_id": session.session_id,
            "qr_code_url": session.qr_code_url,
            "status": status,
            "message": session.message,
            "account_info": session.account_info,
            "expires_at": expires_from + 90,
        }

    async def close(self) -> None:
        for process in tuple(self._processes.values()):
            await terminate_process(process)
        for task in tuple(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        for session in self._sessions.values():
            self._release_operation(session)

    async def _run(self, session: QrAuthSession) -> None:
        try:
            if not await self._wait_for_account_stop(session):
                await self._converge_failed_runtime(session)
                return
            session.qr_started_at = time.time()
            environment = os.environ.copy()
            environment.update(
                {
                    "DB_PATH": str(self._database_path),
                    "XIANYU_PROFILES_ROOT": str(self._profiles_root),
                    "XIANYU_MASTER_KEY_PATH": str(self._master_key_path),
                }
            )
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "xianyu_connector.browser_auth_worker",
                    session.account_id,
                    str(session.user_id),
                    session.session_id,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                    env=environment,
                )
            except OSError:
                session.state = "error"
                session.message = "二维码生成失败，请重新尝试"
                await self._converge_failed_runtime(session)
                return
            self._processes[session.session_id] = process
            stderr_task = asyncio.create_task(drain_stream(process.stderr))
            try:
                await asyncio.wait_for(self._consume_events(session, process), timeout=90)
            except TimeoutError:
                session.state = "expired"
                session.message = "认证超过 90 秒，已终止浏览器进程"
                await terminate_process(process)
            except Exception:
                session.state = "error"
                session.message = "二维码生成失败，请重新尝试"
                await terminate_process(process)
            finally:
                if process.returncode is None:
                    await terminate_process(process)
                await process.wait()
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)
                self._processes.pop(session.session_id, None)
            self._release_operation(session)
            if session.authenticated:
                self._commands.enqueue(
                    session.account_id,
                    AccountCommand.START,
                    f"qr-start:{session.session_id}",
                    {},
                )
                await self._wait_until_online(session)
            elif session.state not in {"error", "expired"}:
                session.state = "error"
                session.message = "认证进程未返回有效结果"
            if session.state in {"error", "expired"}:
                await self._converge_failed_runtime(session)
        finally:
            self._release_operation(session)

    def _release_operation(self, session: QrAuthSession) -> None:
        lease = session.operation_lease
        if lease is None:
            return
        session.operation_lease = None
        self._coordinator.release(lease)

    async def _wait_for_account_stop(self, session: QrAuthSession) -> bool:
        command_id = session.stop_command_id
        if not command_id:
            return True
        deadline = time.monotonic() + ACCOUNT_STOP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            command = self._commands.get(command_id)
            if command and command.status is CommandStatus.SUCCEEDED:
                return True
            if command and command.status is CommandStatus.FAILED:
                session.state = "error"
                session.message = "账号连接进程停止失败，请重新尝试"
                return False
            await asyncio.sleep(0.1)
        session.state = "error"
        session.message = "账号连接进程停止超时，请重新尝试"
        return False

    async def _consume_events(
        self,
        session: QrAuthSession,
        process: asyncio.subprocess.Process,
    ) -> None:
        stream = process.stdout
        if stream is None:
            raise RuntimeError("authentication process stdout is unavailable")
        while line := await stream.readline():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._apply_event(session, event)
        return_code = await process.wait()
        if return_code and session.state not in {"error", "expired"}:
            session.state = "error"
            session.message = f"认证进程退出，状态码 {return_code}"

    def _apply_event(self, session: QrAuthSession, event: dict[str, object]) -> None:
        event_name = str(event.get("event") or "")
        if event_name == "qr":
            session.qr_code_url = str(event.get("qr_code_url") or "")
            session.state = "waiting"
        elif event_name == "status":
            session.state = str(event.get("status") or "waiting")
        elif event_name == "authenticated":
            session.authenticated = True
            session.authenticated_at = time.time()
            session.state = "processing"
            account_info = event.get("account_info")
            session.account_info = dict(account_info) if isinstance(account_info, Mapping) else {}
        elif event_name in {"error", "expired"}:
            session.state = event_name
            session.message = str(event.get("message") or "认证失败")

    async def _wait_until_online(self, session: QrAuthSession) -> None:
        deadline = self._online_deadline(session)
        while time.time() < deadline:
            self._refresh_online_state(session)
            if session.state in {"success", "verification_required"}:
                return
            await asyncio.sleep(0.5)
        await self._stop_after_timeout(session)

    @staticmethod
    def _online_deadline(session: QrAuthSession) -> float:
        authenticated_at = session.authenticated_at
        if authenticated_at is None:
            authenticated_at = time.time()
        return authenticated_at + ONLINE_READINESS_TIMEOUT_SECONDS

    async def _stop_after_timeout(self, session: QrAuthSession) -> None:
        command = self._commands.enqueue(
            session.account_id,
            AccountCommand.STOP,
            f"qr-timeout:{session.session_id}",
            {},
        )
        stop_succeeded = False
        stop_failed = False
        for _ in range(20):
            current = self._commands.get(command.command_id)
            if current and current.status is CommandStatus.SUCCEEDED:
                runtime = self._runtimes.get(session.account_id)
                stop_succeeded = bool(
                    current.result
                    and current.result.get("state") == AccountState.OFFLINE.value
                    and runtime
                    and runtime.state is AccountState.OFFLINE
                    and runtime.worker_pid is None
                )
                stop_failed = not stop_succeeded
                break
            if current and current.status is CommandStatus.FAILED:
                stop_failed = True
                break
            await asyncio.sleep(0.25)
        if not stop_succeeded:
            session.state = "error"
            session.message = (
                "账号连接进程停止失败，请重新尝试"
                if stop_failed
                else "账号连接进程停止超时，请重新尝试"
            )
            return
        runtime = self._runtimes.get(session.account_id)
        if runtime is not None:
            self._runtimes.save_observation(
                runtime,
                replace(
                    runtime,
                    reason_code="authentication_timeout",
                    reason_message="扫码后 90 秒内未达到在线状态，请重新生成二维码",
                ),
            )
        session.state = "error"
        session.message = "账号登录校验超时，请重新生成二维码"

    def _refresh_online_state(self, session: QrAuthSession) -> None:
        if not session.authenticated:
            return
        runtime = self._runtimes.get(session.account_id)
        if runtime and runtime.state is AccountState.ONLINE and runtime.readiness.online:
            session.state = "success"
            session.message = "账号四项在线检查已全部通过"
        elif runtime and runtime.state is AccountState.MANUAL_VERIFICATION_REQUIRED:
            session.state = "verification_required"
            session.message = runtime.reason_message or "需要人工验证"

def _public_status(state: str) -> str:
    return {
        "new": "waiting",
        "confirmed": "confirmed",
        "authenticated": "processing",
    }.get(state, state)
