from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from xianyu_connector.domain.verification_session import (
    VerificationEvent,
    VerificationSession,
    VerificationSessionState,
)
from xianyu_connector.infrastructure.sqlite_verification_repository import (
    VerificationRepository,
)


class RuntimeVerificationPort(Protocol):
    def require_manual_verification(self, reason_code: str, message: str) -> object: ...


class AccountRecoveryPort(Protocol):
    def resume_after_verification(self, account_id: str, session_id: str) -> object: ...


class VerificationSessionLifecycleMixin:
    _repository: VerificationRepository
    _idle_timeout_seconds: int
    _runtime_port: RuntimeVerificationPort | None
    _recovery_port: AccountRecoveryPort | None

    def get(
        self,
        session_id: str,
        *,
        account_id: str | None = None,
        now: datetime | None = None,
    ) -> VerificationSession:
        raise NotImplementedError

    def authenticate(
        self,
        session_id: str,
        access_token: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        raise NotImplementedError

    def start(
        self,
        session_id: str,
        *,
        worker_pid: int | None = None,
        now: datetime | None = None,
    ) -> VerificationSession:
        return self._transition(
            session_id,
            VerificationSessionState.STARTING,
            worker_pid=worker_pid,
            now=now,
        )

    def mark_waiting(self, session_id: str, *, now: datetime | None = None) -> VerificationSession:
        return self._transition(session_id, VerificationSessionState.WAITING_FOR_OPERATOR, now=now)

    def activate(
        self,
        session_id: str,
        access_token: str,
        *,
        now: datetime | None = None,
    ) -> VerificationSession:
        current_time = _utc(now or datetime.now(UTC))
        session = self.get(session_id, now=current_time)
        if session.state is not VerificationSessionState.WAITING_FOR_OPERATOR:
            raise InvalidVerificationToken("verification session is not accepting operator access")
        if not self.authenticate(session_id, access_token, now=current_time):
            raise InvalidVerificationToken("verification access token is invalid or expired")
        return self._transition(
            session_id,
            VerificationSessionState.OPERATOR_ACTIVE,
            now=current_time,
        )

    def submit(self, session_id: str, *, now: datetime | None = None) -> VerificationSession:
        return self._transition(session_id, VerificationSessionState.SUBMITTED, now=now)

    def begin_verification(
        self,
        session_id: str,
        *,
        now: datetime | None = None,
    ) -> VerificationSession:
        return self._transition(session_id, VerificationSessionState.VERIFYING, now=now)

    def complete(
        self,
        session_id: str,
        *,
        success: bool,
        reason_code: str | None = None,
        reason_message: str | None = None,
        now: datetime | None = None,
    ) -> VerificationSession:
        target = VerificationSessionState.SUCCEEDED if success else VerificationSessionState.FAILED
        session = self._transition(
            session_id,
            target,
            reason_code=reason_code,
            reason_message=reason_message,
            now=now,
        )
        if success and self._recovery_port:
            self._recovery_port.resume_after_verification(session.account_id, session.session_id)
        if not success and self._runtime_port:
            self._runtime_port.require_manual_verification(
                reason_code or "verification_failed",
                reason_message or "人工验证未通过",
            )
        return session

    def cancel(self, session_id: str, *, now: datetime | None = None) -> VerificationSession:
        return self._transition(session_id, VerificationSessionState.CANCELLED, now=now)

    def require_manual_device(
        self,
        session_id: str,
        *,
        reason_code: str = "manual_device_required",
        reason_message: str = "需要在原设备完成验证",
        now: datetime | None = None,
    ) -> VerificationSession:
        return self._transition(
            session_id,
            VerificationSessionState.MANUAL_DEVICE_REQUIRED,
            reason_code=reason_code,
            reason_message=reason_message,
            now=now,
        )

    def touch(self, session_id: str, *, now: datetime | None = None) -> VerificationSession:
        session = self.get(session_id, now=now)
        if session.terminal:
            return session
        return self._repository.touch(session.touch(_utc(now or datetime.now(UTC))))

    def expire_stale(self, *, now: datetime | None = None) -> int:
        current_time = _utc(now or datetime.now(UTC))
        expired = 0
        for session in self._repository.list_active():
            if self._expire_if_needed(session, current_time).state is VerificationSessionState.EXPIRED:
                expired += 1
        return expired

    def events(self, session_id: str) -> list[VerificationEvent]:
        self._require(session_id)
        return self._repository.events(session_id)

    def _transition(
        self,
        session_id: str,
        target: VerificationSessionState,
        *,
        now: datetime | None = None,
        reason_code: str | None = None,
        reason_message: str | None = None,
        worker_pid: int | None = None,
    ) -> VerificationSession:
        current_time = _utc(now or datetime.now(UTC))
        session = self._expire_if_needed(self._require(session_id), current_time)
        current = session.transition(
            target,
            now=current_time,
            reason_code=reason_code,
            reason_message=reason_message,
            worker_pid=worker_pid,
        )
        self._repository.save_transition(session, current)
        return current

    def _expire_if_needed(self, session: VerificationSession, now: datetime) -> VerificationSession:
        if session.terminal:
            return session
        if not (
            session.is_expired(now)
            or session.idle_expired_at(now, self._idle_timeout_seconds)
        ):
            return session
        expired = session.transition(
            VerificationSessionState.EXPIRED,
            now=now,
            reason_code="verification_timeout",
            reason_message="人工验证会话已超时",
        )
        self._repository.save_transition(session, expired)
        return expired

    def _require(self, session_id: str) -> VerificationSession:
        session = self._repository.get(session_id)
        if not session:
            raise VerificationSessionNotFound(session_id)
        return session


class InvalidVerificationToken(ValueError):
    pass


class VerificationSessionNotFound(LookupError):
    pass


def _utc(value: datetime) -> datetime:
    normalized = value if value.tzinfo else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC)
