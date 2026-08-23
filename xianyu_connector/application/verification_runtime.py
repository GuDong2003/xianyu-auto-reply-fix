from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote

from xianyu_connector.application.account_operation_coordinator import (
    AccountOperationConflict,
    AccountOperationCoordinator,
    AccountOperationKind,
    AccountOperationLease,
)
from xianyu_connector.application.verification_session_manager import (
    InvalidVerificationToken,
    VerificationSessionConflict,
    VerificationSessionManager,
    VerificationSessionNotFound,
)
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.domain.commands import AccountCommand
from xianyu_connector.domain.verification_session import (
    VerificationSession,
    VerificationSessionState,
)
from xianyu_connector.infrastructure.sqlite_command_repository import SqliteCommandRepository
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository
from xianyu_connector.infrastructure.sqlite_secret_repository import SqliteSecretRepository
from xianyu_connector.infrastructure.sqlite_verification_repository import (
    SqliteVerificationRepository,
)
from xianyu_connector.infrastructure.verification_process import (
    RfbConnectionLease,
    VerificationProcessSupervisor,
)
from xianyu_connector.security.aes_gcm import SecretCipher, load_master_key


class VerificationUnavailable(RuntimeError):
    pass


class VerificationRfbForbidden(PermissionError):
    pass


class VerificationCoordinator:
    def __init__(
        self,
        database_path: Path,
        profiles_root: Path,
        master_key_path: Path,
        runtime_repository: SqliteRuntimeRepository,
        command_repository: SqliteCommandRepository,
        *,
        coordinator: AccountOperationCoordinator | None = None,
    ) -> None:
        cipher = SecretCipher(load_master_key(master_key_path))
        self._secrets = SqliteSecretRepository(database_path, cipher)
        self._runtimes = runtime_repository
        self._commands = command_repository
        self._coordinator = coordinator or AccountOperationCoordinator()
        self._sessions = VerificationSessionManager(
            SqliteVerificationRepository(database_path, cipher),
        )
        self._processes = VerificationProcessSupervisor(profiles_root)
        self._recovery_tasks: dict[str, asyncio.Task[None]] = {}
        self._recovery_commands: dict[str, str] = {}
        self._operation_leases: dict[str, AccountOperationLease] = {}

    def create(self, account_id: str, operator_id: str, idempotency_key: str) -> dict[str, Any]:
        runtime = self._runtimes.get(account_id)
        if not runtime or runtime.state is not AccountState.MANUAL_VERIFICATION_REQUIRED:
            raise VerificationUnavailable("账号当前没有待处理的人工验证")
        challenge_url = self._secrets.get(account_id, "verification_url") or ""
        if not challenge_url:
            raise VerificationUnavailable("没有可用的验证会话，请重新扫码")
        active_kind = self._coordinator.active_kind(account_id)
        lease: AccountOperationLease | None = None
        if active_kind is None:
            try:
                lease = self._coordinator.reserve(
                    account_id,
                    AccountOperationKind.MANUAL_VERIFICATION,
                )
            except AccountOperationConflict as exc:
                raise VerificationUnavailable("账号正在执行其他操作，请稍后重试") from exc
        try:
            result = self._sessions.create(
                account_id,
                operator_id,
                idempotency_key,
                challenge_info=challenge_url,
            )
        except VerificationSessionConflict as exc:
            if lease is not None:
                self._coordinator.release(lease)
            raise VerificationUnavailable("账号已有其他人工验证会话") from exc
        if result.created:
            session_id = result.session.session_id
            try:
                self._sessions.start(session_id)
                self._processes.start(account_id, session_id, challenge_url)
                session = self._sessions.mark_waiting(session_id)
            except Exception as exc:
                with suppress(Exception):
                    self._sessions.complete(
                        session_id,
                        success=False,
                        reason_code="browser_start_failed",
                        reason_message="验证浏览器启动失败，请重新发起验证",
                    )
                if lease is not None:
                    self._coordinator.release(lease)
                    lease = None
                raise VerificationUnavailable("验证浏览器启动失败，请重新发起验证") from exc
        else:
            session = result.session
        if lease is not None and result.created:
            self._operation_leases[session.session_id] = lease
        elif lease is not None:
            self._coordinator.release(lease)
        return {**_public_session(session), "access_token": result.access_token}

    async def create_async(
        self,
        account_id: str,
        operator_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        async with self._coordinator.hold_async(account_id):
            return await asyncio.to_thread(
                self.create,
                account_id,
                operator_id,
                idempotency_key,
            )

    def get(self, account_id: str, session_id: str, ticket: str | None, after_seq: int) -> dict[str, Any]:
        del after_seq
        session = self._authorize(account_id, session_id, ticket)
        process = self._processes.get(account_id, session_id)
        return self._process_payload(session, process)

    def frame(self, account_id: str, session_id: str, ticket: str | None, after_seq: int) -> dict[str, Any]:
        del after_seq
        session = self._authorize(account_id, session_id, ticket)
        process = self._processes.get(account_id, session_id)
        return self._process_payload(session, process)

    def input(
        self,
        account_id: str,
        session_id: str,
        ticket: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        del payload
        self._authorize(account_id, session_id, ticket)
        raise VerificationUnavailable("验证输入已切换到受保护的 RFB 通道")

    def open_rfb(
        self,
        account_id: str,
        session_id: str,
        operator_id: str,
    ) -> RfbConnectionLease:
        session = self._sessions.get(session_id)
        if session.account_id != account_id:
            raise VerificationSessionNotFound(session_id)
        if session.operator_id != operator_id:
            raise VerificationRfbForbidden("verification session belongs to another operator")
        if session.state is not VerificationSessionState.OPERATOR_ACTIVE:
            raise VerificationUnavailable("验证会话当前不接受 RFB 连接")
        process = self._processes.get(account_id, session_id)
        if process is None:
            raise VerificationUnavailable("验证浏览器已退出，请重新发起验证")
        return process.acquire_rfb(
            touch_callback=lambda: self._touch_session(session_id),
        )

    def _touch_session(self, session_id: str) -> None:
        self._sessions.touch(session_id)

    def complete(
        self,
        account_id: str,
        session_id: str,
        ticket: str | None,
    ) -> dict[str, Any]:
        session = self._authorize(account_id, session_id, ticket)
        if session.state not in {
            VerificationSessionState.OPERATOR_ACTIVE,
            VerificationSessionState.WAITING_FOR_OPERATOR,
        }:
            return _public_session(session)
        process = self._processes.get(account_id, session_id)
        if process is None:
            raise VerificationUnavailable("验证浏览器已退出，请重新发起验证")
        try:
            process.complete()
        except RuntimeError as exc:
            with suppress(Exception):
                self._sessions.complete(
                    session_id,
                    success=False,
                    reason_code="browser_completion_failed",
                    reason_message="验证浏览器未能保存验证结果",
                )
            raise VerificationUnavailable("验证浏览器未能保存验证结果") from exc
        self._sessions.submit(session_id)
        verifying = self._sessions.begin_verification(session_id)
        self._processes.stop(account_id, session_id)
        self._release_operation(session_id)
        self._start_recovery(account_id, session_id)
        return _public_session(verifying)

    async def complete_async(
        self,
        account_id: str,
        session_id: str,
        ticket: str | None,
    ) -> dict[str, Any]:
        async with self._coordinator.hold_async(account_id):
            return await asyncio.to_thread(
                self.complete,
                account_id,
                session_id,
                ticket,
            )

    def cancel(self, account_id: str, session_id: str, ticket: str | None) -> dict[str, Any]:
        session = self._authorize(account_id, session_id, ticket)
        if session.terminal:
            return _public_session(session)
        self._processes.stop(account_id, session_id)
        cancelled = self._sessions.cancel(session_id)
        self._release_operation(session_id)
        return _public_session(cancelled)

    async def stop_for_qr(self, account_id: str) -> None:
        self._secrets.save(account_id, "verification_url", "")
        session = self._sessions.active_for_account(account_id)
        if session:
            recovery_task = self._recovery_tasks.pop(session.session_id, None)
            if recovery_task and not recovery_task.done():
                recovery_task.cancel()
            recovery_command_id = self._recovery_commands.pop(session.session_id, None)
            if recovery_command_id:
                self._commands.cancel(
                    recovery_command_id,
                    error_message="验证会话已被二维码登录替换",
                )
        await asyncio.to_thread(
            self._processes.stop,
            account_id,
            session.session_id if session else None,
        )
        if not session:
            return
        self._release_operation(session.session_id)
        current = self._sessions.get(session.session_id)
        if current.terminal:
            return
        if current.state in {
            VerificationSessionState.SUBMITTED,
            VerificationSessionState.VERIFYING,
        }:
            self._sessions.complete(
                current.session_id,
                success=False,
                reason_code="replaced_by_qr_login",
                reason_message="二维码登录已开始，原人工验证会话已结束",
            )
            return
        self._sessions.cancel(current.session_id)

    def close(self) -> None:
        self._processes.stop_all()
        for task in self._recovery_tasks.values():
            task.cancel()
        self._recovery_tasks.clear()
        self._recovery_commands.clear()
        for session_id in tuple(self._operation_leases):
            self._release_operation(session_id)

    def _authorize(
        self,
        account_id: str,
        session_id: str,
        ticket: str | None,
    ) -> VerificationSession:
        session = self._sessions.get(session_id)
        if session.account_id != account_id:
            raise VerificationSessionNotFound(session_id)
        if session.terminal:
            self._processes.stop(account_id, session_id)
            self._release_operation(session_id)
            return session
        if session.state is VerificationSessionState.WAITING_FOR_OPERATOR:
            if not ticket:
                raise InvalidVerificationToken("verification ticket is required")
            return self._sessions.activate(session_id, ticket)
        if session.state in {
            VerificationSessionState.OPERATOR_ACTIVE,
            VerificationSessionState.SUBMITTED,
            VerificationSessionState.VERIFYING,
        }:
            return session
        raise VerificationUnavailable("验证会话当前不可操作")

    def _process_payload(
        self,
        session: VerificationSession,
        process: Any,
    ) -> dict[str, Any]:
        payload = _public_session(session)
        if process:
            state = process.get_state()
            if state:
                payload["browser_state"] = state.get("state")
            error = process.get_error()
            if error:
                payload["failure_code"] = error.get("code")
        return payload

    def _start_recovery(self, account_id: str, session_id: str) -> None:
        if session_id in self._recovery_tasks:
            return
        self._recovery_tasks[session_id] = asyncio.create_task(
            self._recover_account(account_id, session_id)
        )

    def _release_operation(self, session_id: str) -> None:
        lease = self._operation_leases.pop(session_id, None)
        if lease is not None:
            self._coordinator.release(lease)

    async def _recover_account(self, account_id: str, session_id: str) -> None:
        try:
            command = self._commands.enqueue(
                account_id,
                AccountCommand.RESUME_AFTER_VERIFICATION,
                f"verification-resume:{session_id}",
                {"verification_session_id": session_id},
            )
            self._recovery_commands[session_id] = command.command_id
            deadline = asyncio.get_running_loop().time() + 90
            while asyncio.get_running_loop().time() < deadline:
                runtime = self._runtimes.get(account_id)
                if runtime and runtime.state is AccountState.ONLINE and runtime.readiness.online:
                    self._sessions.complete(session_id, success=True)
                    self._secrets.save(account_id, "verification_url", "")
                    return
                if runtime and runtime.state is AccountState.MANUAL_VERIFICATION_REQUIRED:
                    break
                await asyncio.sleep(0.5)
            self._sessions.complete(
                session_id,
                success=False,
                reason_code="recovery_failed",
                reason_message="验证完成后账号未通过四项在线检查",
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            with suppress(Exception):
                self._sessions.complete(
                    session_id,
                    success=False,
                    reason_code="recovery_error",
                    reason_message="验证恢复过程失败，请重新验证",
                )
        finally:
            self._processes.stop(account_id, session_id)
            self._recovery_tasks.pop(session_id, None)
            self._recovery_commands.pop(session_id, None)


def _public_session(session: VerificationSession) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "account_id": session.account_id,
        "state": session.state.value,
        "reason_code": session.reason_code,
        "reason_message": session.reason_message,
        "expires_at": session.expires_at.isoformat(),
        "frame_seq": 0,
        "transport": "rfb",
        "rfb_websocket_path": (
            f"/internal/accounts/{quote(session.account_id, safe='')}/verification-sessions/"
            f"{quote(session.session_id, safe='')}/rfb"
        ),
    }
