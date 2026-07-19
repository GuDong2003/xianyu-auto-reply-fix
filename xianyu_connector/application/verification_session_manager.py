from __future__ import annotations

import hashlib
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from xianyu_connector.application.verification_session_lifecycle import (
    AccountRecoveryPort,
    RuntimeVerificationPort,
    VerificationSessionLifecycleMixin,
)
from xianyu_connector.application.verification_session_lifecycle import (
    InvalidVerificationToken as _InvalidVerificationToken,
)
from xianyu_connector.application.verification_session_lifecycle import (
    VerificationSessionNotFound as _VerificationSessionNotFound,
)
from xianyu_connector.domain.verification_session import (
    VerificationSession,
    VerificationSessionState,
)
from xianyu_connector.infrastructure.sqlite_verification_repository import (
    VerificationRepository,
)

InvalidVerificationToken = _InvalidVerificationToken
VerificationSessionNotFound = _VerificationSessionNotFound


@dataclass(frozen=True, slots=True)
class VerificationSessionResult:
    session: VerificationSession
    access_token: str
    created: bool


class VerificationSessionConflict(RuntimeError):
    pass


class VerificationSessionManager(VerificationSessionLifecycleMixin):
    def __init__(
        self,
        repository: VerificationRepository,
        *,
        ttl_seconds: int = 600,
        idle_timeout_seconds: int = 300,
        replay_token_ttl_seconds: int = 60,
        runtime_port: RuntimeVerificationPort | None = None,
        recovery_port: AccountRecoveryPort | None = None,
    ) -> None:
        if ttl_seconds <= 0 or idle_timeout_seconds <= 0 or replay_token_ttl_seconds <= 0:
            raise ValueError("verification TTLs must be positive")
        self._repository = repository
        self._ttl_seconds = ttl_seconds
        self._idle_timeout_seconds = idle_timeout_seconds
        self._replay_token_ttl_seconds = replay_token_ttl_seconds
        self._runtime_port = runtime_port
        self._recovery_port = recovery_port

    @staticmethod
    def new_session(
        session_id: str,
        account_id: str,
        operator_id: str | int,
        created_at: datetime,
        expires_at: datetime,
        *,
        idempotency_key: str = "",
        challenge_info: str | None = None,
    ) -> VerificationSession:
        now = _utc(created_at)
        return VerificationSession(
            session_id=session_id,
            account_id=account_id,
            operator_id=str(operator_id),
            state=VerificationSessionState.REQUESTED,
            created_at=now,
            expires_at=_utc(expires_at),
            last_activity_at=now,
            idempotency_key=idempotency_key,
            challenge_info=challenge_info,
        )

    def create(
        self,
        account_id: str,
        operator_id: str | int,
        idempotency_key: str,
        *,
        challenge_info: str | None = None,
        now: datetime | None = None,
    ) -> VerificationSessionResult:
        normalized_operator_id = str(operator_id)
        normalized_idempotency_key = idempotency_key.strip()
        if not normalized_idempotency_key:
            raise ValueError("idempotency_key is required")
        current_time = _utc(now or datetime.now(UTC))
        existing = self._repository.get_by_idempotency(
            account_id,
            normalized_operator_id,
            normalized_idempotency_key,
        )
        if existing:
            existing = self._expire_if_needed(existing, current_time)
            if existing.active:
                return VerificationSessionResult(
                    existing,
                    self._issue_replay_token(existing, current_time),
                    False,
                )
            return VerificationSessionResult(existing, "", False)
        active = self._repository.get_active_for_account(account_id)
        if active:
            active = self._expire_if_needed(active, current_time)
            if active.active:
                raise VerificationSessionConflict(
                    "account already has an active verification session"
                )
        session = self.new_session(
            uuid.uuid4().hex,
            account_id,
            normalized_operator_id,
            current_time,
            current_time + timedelta(seconds=self._ttl_seconds),
            idempotency_key=normalized_idempotency_key,
            challenge_info=challenge_info,
        )
        token = secrets.token_urlsafe(32)
        try:
            self._repository.create_with_access_token(
                session,
                _hash_token(token),
                current_time,
            )
        except Exception as exc:
            if _is_integrity_error(exc):
                existing = self._repository.get_by_idempotency(
                    account_id,
                    normalized_operator_id,
                    normalized_idempotency_key,
                )
                active = self._repository.get_active_for_account(account_id)
                if existing:
                    existing = self._expire_if_needed(existing, current_time)
                    if existing.active:
                        return VerificationSessionResult(
                            existing,
                            self._issue_replay_token(existing, current_time),
                            False,
                        )
                    return VerificationSessionResult(existing, "", False)
                if active and self._expire_if_needed(active, current_time).active:
                    raise VerificationSessionConflict(
                        "account already has an active verification session"
                    ) from exc
            raise
        return VerificationSessionResult(session, token, True)

    def _issue_replay_token(
        self,
        session: VerificationSession,
        current_time: datetime,
    ) -> str:
        expires_at = min(
            session.expires_at,
            current_time + timedelta(seconds=self._replay_token_ttl_seconds),
        )
        if expires_at <= current_time:
            raise VerificationSessionConflict("verification session is no longer active")
        token = secrets.token_urlsafe(32)
        self._repository.create_access_token(
            session.session_id,
            _hash_token(token),
            current_time,
            expires_at,
        )
        return token

    def request(
        self,
        account_id: str,
        operator_id: str | int,
        idempotency_key: str,
        *,
        challenge_info: str | None = None,
        now: datetime | None = None,
    ) -> VerificationSessionResult:
        return self.create(
            account_id,
            operator_id,
            idempotency_key,
            challenge_info=challenge_info,
            now=now,
        )

    def get(
        self,
        session_id: str,
        *,
        account_id: str | None = None,
        now: datetime | None = None,
    ) -> VerificationSession:
        session = self._require(session_id)
        if account_id is not None and session.account_id != account_id:
            raise VerificationSessionNotFound(session_id)
        return self._expire_if_needed(session, _utc(now or datetime.now(UTC)))

    def active_for_account(
        self,
        account_id: str,
        *,
        now: datetime | None = None,
    ) -> VerificationSession | None:
        session = self._repository.get_active_for_account(account_id)
        if session is None:
            return None
        return self._expire_if_needed(session, _utc(now or datetime.now(UTC)))

    def authenticate(
        self,
        session_id: str,
        access_token: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        session = self.get(session_id, now=now)
        if session.terminal:
            return False
        return self._repository.consume_access_token(
            session.session_id,
            _hash_token(access_token),
            _utc(now or datetime.now(UTC)),
        )

def _hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _utc(value: datetime) -> datetime:
    normalized = value if value.tzinfo else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC)


def _is_integrity_error(error: Exception) -> bool:
    return isinstance(error, sqlite3.IntegrityError) or "UNIQUE" in str(error).upper()
