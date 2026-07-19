from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any

from xianyu_connector.application.verification_session_manager import (
    VerificationSessionManager,
    VerificationSessionNotFound,
)
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.domain.commands import AccountCommand, CommandRecord, CommandStatus
from xianyu_connector.domain.verification_session import (
    InvalidVerificationTransition,
    VerificationSession,
    VerificationSessionState,
)
from xianyu_connector.infrastructure.local_handoff_repository import (
    LocalHandoffConflict,
    LocalHandoffGone,
    LocalHandoffNotFound,
    SqliteLocalHandoffRepository,
)
from xianyu_connector.infrastructure.sqlite_command_repository import (
    SqliteCommandRepository,
)
from xianyu_connector.infrastructure.sqlite_runtime_repository import (
    SqliteRuntimeRepository,
)
from xianyu_connector.infrastructure.sqlite_secret_repository import (
    SqliteSecretRepository,
)
from xianyu_connector.infrastructure.sqlite_verification_repository import (
    SqliteVerificationRepository,
    VerificationVersionConflict,
)
from xianyu_connector.infrastructure.verification_browser import validate_challenge_url
from xianyu_connector.security.aes_gcm import SecretCipher, load_master_key

LOCAL_HANDOFF_LAUNCH_TTL_SECONDS = 60


class LocalHandoffDisabled(RuntimeError):
    pass


class LocalHandoffOperatorMismatch(PermissionError):
    pass


class LocalVerificationHandoff:
    def __init__(
        self,
        database_path: Path,
        master_key_path: Path,
        runtime_repository: SqliteRuntimeRepository,
        command_repository: SqliteCommandRepository,
        *,
        enabled: bool,
        completion_ttl_seconds: int = 600,
        recovery_timeout_seconds: float = 90,
        recovery_poll_seconds: float = 0.5,
    ) -> None:
        if completion_ttl_seconds <= 0:
            raise ValueError("local handoff completion TTL must be positive")
        cipher = SecretCipher(load_master_key(master_key_path))
        self._enabled = enabled
        self._completion_ttl_seconds = completion_ttl_seconds
        self._recovery_timeout_seconds = recovery_timeout_seconds
        self._recovery_poll_seconds = recovery_poll_seconds
        self._runtimes = runtime_repository
        self._commands = command_repository
        self._secrets = SqliteSecretRepository(database_path, cipher)
        self._sessions = VerificationSessionManager(
            SqliteVerificationRepository(database_path, cipher),
            idle_timeout_seconds=completion_ttl_seconds,
        )
        self._grants = SqliteLocalHandoffRepository(database_path, cipher)
        self._recovery_tasks: dict[str, asyncio.Task[None]] = {}

    def create(
        self,
        account_id: str,
        operator_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        runtime = self._runtimes.get(account_id)
        if not runtime or runtime.state is not AccountState.MANUAL_VERIFICATION_REQUIRED:
            raise LocalHandoffConflict("account has no pending manual verification")
        challenge_url = self._secrets.get(account_id, "verification_url") or ""
        validate_challenge_url(challenge_url)
        self._sessions.expire_stale()
        grant = self._grants.create_or_get(
            account_id,
            operator_id,
            idempotency_key,
            challenge_url,
            ttl_seconds=LOCAL_HANDOFF_LAUNCH_TTL_SECONDS,
        )
        return {
            **_public_session(grant.session),
            "handoff_token": grant.token,
        }

    def consume(self, account_id: str, session_id: str, token: str) -> str:
        self._require_enabled()
        session = self._grants.get_session(account_id, session_id)
        challenge_url = validate_challenge_url(session.challenge_info or "")
        current_challenge = validate_challenge_url(
            self._secrets.get(account_id, "verification_url") or ""
        )
        if current_challenge != challenge_url:
            self._sessions.require_manual_device(
                session_id,
                reason_code="new_challenge",
                reason_message="平台返回了新的验证挑战",
            )
            self._grants.revoke(session_id)
            raise LocalHandoffGone("local handoff challenge has changed")
        self._grants.consume_and_activate(
            account_id,
            session_id,
            token,
            completion_ttl_seconds=self._completion_ttl_seconds,
        )
        return challenge_url

    def complete(
        self,
        account_id: str,
        session_id: str,
        operator_id: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        self._grants.get_session(account_id, session_id)
        session = self._sessions.get(session_id, account_id=account_id)
        if session.operator_id != operator_id:
            raise LocalHandoffOperatorMismatch("local handoff belongs to another operator")
        if session.terminal:
            return _public_session(session)
        if session.state in {
            VerificationSessionState.SUBMITTED,
            VerificationSessionState.VERIFYING,
        }:
            command = self._enqueue_recovery(account_id, session_id)
            self._start_recovery(account_id, session, command.command_id)
            return _public_session(session)
        if session.state is not VerificationSessionState.OPERATOR_ACTIVE:
            raise LocalHandoffConflict("local handoff has not been consumed")

        command = self._enqueue_recovery(account_id, session_id)
        verifying = self._advance_to_verifying(session_id)
        self._start_recovery(account_id, verifying, command.command_id)
        return _public_session(verifying)

    def close(self) -> None:
        for task in self._recovery_tasks.values():
            task.cancel()
        self._recovery_tasks.clear()

    def _advance_to_verifying(self, session_id: str) -> VerificationSession:
        try:
            self._sessions.submit(session_id)
            return self._sessions.begin_verification(session_id)
        except (InvalidVerificationTransition, VerificationVersionConflict):
            current = self._sessions.get(session_id)
            if current.state in {
                VerificationSessionState.SUBMITTED,
                VerificationSessionState.VERIFYING,
            }:
                return current
            raise

    def _enqueue_recovery(self, account_id: str, session_id: str) -> CommandRecord:
        return self._commands.enqueue(
            account_id,
            AccountCommand.RESUME_AFTER_VERIFICATION,
            f"local-verification-resume:{session_id}",
            {"verification_session_id": session_id, "handoff_mode": "local"},
        )

    def _start_recovery(
        self,
        account_id: str,
        session: VerificationSession,
        command_id: str,
    ) -> None:
        if session.session_id in self._recovery_tasks or session.terminal:
            return
        challenge_url = validate_challenge_url(session.challenge_info or "")
        self._recovery_tasks[session.session_id] = asyncio.create_task(
            self._observe_recovery(
                account_id,
                session.session_id,
                command_id,
                challenge_url,
            )
        )

    async def _observe_recovery(
        self,
        account_id: str,
        session_id: str,
        command_id: str,
        challenge_url: str,
    ) -> None:
        deadline = asyncio.get_running_loop().time() + self._recovery_timeout_seconds
        left_manual_state = False
        try:
            while asyncio.get_running_loop().time() < deadline:
                runtime = self._runtimes.get(account_id)
                command = self._commands.get(command_id)
                if command and command.status is CommandStatus.FAILED:
                    self._fail(session_id, "recovery_command_failed", "账号恢复命令执行失败")
                    return
                current_challenge = self._secrets.get(account_id, "verification_url") or ""
                if current_challenge and current_challenge != challenge_url:
                    self._fail(session_id, "new_challenge", "平台返回了新的验证挑战")
                    return
                if runtime and runtime.state is AccountState.ONLINE and runtime.readiness.online:
                    self._sessions.complete(session_id, success=True)
                    self._secrets.save(account_id, "verification_url", "")
                    return
                if runtime and runtime.state in {
                    AccountState.DISABLED,
                    AccountState.OFFLINE,
                    AccountState.PAUSED,
                    AccountState.FAILED,
                }:
                    self._fail(session_id, "recovery_failed", "账号恢复已终止")
                    return
                if runtime and runtime.state is not AccountState.MANUAL_VERIFICATION_REQUIRED:
                    left_manual_state = True
                elif left_manual_state:
                    self._fail(session_id, "recovery_failed", "平台仍要求人工验证")
                    return
                await asyncio.sleep(self._recovery_poll_seconds)
            self._fail(session_id, "recovery_timeout", "验证后账号未通过四项在线检查")
        except asyncio.CancelledError:
            raise
        except Exception:
            with suppress(Exception):
                self._fail(session_id, "recovery_error", "验证恢复过程失败")
        finally:
            self._recovery_tasks.pop(session_id, None)

    def _fail(self, session_id: str, reason_code: str, reason_message: str) -> None:
        self._sessions.complete(
            session_id,
            success=False,
            reason_code=reason_code,
            reason_message=reason_message,
        )

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise LocalHandoffDisabled("local verification handoff is disabled")


def _public_session(session: VerificationSession) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "account_id": session.account_id,
        "state": session.state.value,
        "reason_code": session.reason_code,
        "reason_message": session.reason_message,
        "expires_at": session.expires_at.isoformat(),
        "mode": "local_handoff",
    }


__all__ = [
    "LOCAL_HANDOFF_LAUNCH_TTL_SECONDS",
    "LocalHandoffConflict",
    "LocalHandoffDisabled",
    "LocalHandoffGone",
    "LocalHandoffNotFound",
    "LocalHandoffOperatorMismatch",
    "LocalVerificationHandoff",
    "VerificationSessionNotFound",
]
