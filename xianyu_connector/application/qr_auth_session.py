from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xianyu_connector.application.account_operation_coordinator import (
        AccountOperationLease,
    )

from xianyu_connector.application.runtime_service import RuntimeService
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.infrastructure.async_process import terminate_process
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository

SAFE_QR_FAILURE_MESSAGES = frozenset(
    {
        "二维码生成超时，请重新尝试",
        "账号连接进程未在认证前退出，请重新尝试",
        "账号连接进程停止失败，请重新尝试",
        "账号连接进程停止超时，请重新尝试",
        "验证浏览器停止失败，请重新尝试",
    }
)


@dataclass(slots=True)
class QrAuthSession:
    session_id: str
    account_id: str
    user_id: int
    state: str = "pending"
    qr_code_url: str | None = None
    message: str | None = None
    account_info: dict[str, object] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    authenticated: bool = False
    authenticated_at: float | None = None
    stop_command_id: str | None = None
    qr_started_at: float | None = None
    operation_lease: AccountOperationLease | None = None


class QrAuthSessionLifecycle:
    _qr_generation_timeout_seconds: float
    _runtimes: SqliteRuntimeRepository
    _sessions: dict[str, QrAuthSession]
    _tasks: dict[str, asyncio.Task[None]]
    _processes: dict[str, asyncio.subprocess.Process]

    async def _wait_for_qr(self, session: QrAuthSession, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if session.qr_code_url or session.state in {"error", "expired"}:
                return
            await asyncio.sleep(0.1)

    async def _await_qr_or_fail(self, session: QrAuthSession) -> None:
        while (
            session.qr_started_at is None
            and not session.qr_code_url
            and session.state not in {"error", "expired"}
        ):
            await asyncio.sleep(0.1)
        if session.qr_code_url or session.state in {"error", "expired"}:
            return
        qr_started_at = session.qr_started_at
        if qr_started_at is None:
            return
        remaining = max(
            0.0,
            qr_started_at + self._qr_generation_timeout_seconds - time.time(),
        )
        await self._wait_for_qr(session, timeout=remaining)
        if session.qr_code_url or session.state in {"error", "expired"}:
            return
        session.state = "error"
        session.message = "二维码生成超时，请重新尝试"
        process = self._processes.get(session.session_id)
        task = self._tasks.get(session.session_id)
        if process is not None:
            await terminate_process(process)
        elif task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._release_operation(session)
        await self._converge_failed_runtime(session)

    def _release_operation(self, session: QrAuthSession) -> None:
        raise NotImplementedError

    async def _converge_failed_runtime(self, session: QrAuthSession) -> None:
        reason_code, message = _safe_failure(session)
        session.message = message
        for _ in range(50):
            runtime = self._runtimes.get(session.account_id)
            if runtime and runtime.state is AccountState.QR_PENDING:
                RuntimeService(self._runtimes).transition_to(
                    session.account_id,
                    AccountState.OFFLINE,
                    reason_code=reason_code,
                    reason_message=message,
                    clear_readiness=True,
                )
                return
            if runtime and runtime.state is AccountState.OFFLINE:
                return
            if runtime is None:
                return
            await asyncio.sleep(0.1)

    def _recover_orphaned_qr_states(self) -> None:
        service = RuntimeService(self._runtimes)
        for runtime in self._runtimes.list_all():
            if runtime.state is AccountState.QR_PENDING:
                service.transition_to(
                    runtime.account_id,
                    AccountState.OFFLINE,
                    reason_code="qr_session_lost",
                    reason_message="二维码会话已中断，请重新生成",
                    clear_readiness=True,
                )

    def _active_for_account(self, account_id: str) -> QrAuthSession | None:
        terminal = {"success", "error", "expired", "verification_required"}
        return next(
            (
                session
                for session in self._sessions.values()
                if session.account_id == account_id and session.state not in terminal
            ),
            None,
        )


def _safe_failure(session: QrAuthSession) -> tuple[str, str]:
    if session.state == "expired":
        return "qr_expired", "二维码已过期，请重新尝试"
    message = session.message
    if message in SAFE_QR_FAILURE_MESSAGES:
        return "qr_auth_failed", message
    return "qr_auth_failed", "二维码生成失败，请重新尝试"
