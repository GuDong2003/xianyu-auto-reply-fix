from __future__ import annotations

import hashlib
import secrets
import sqlite3
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from xianyu_connector.application.verification_session_manager import (
    VerificationSessionManager,
)
from xianyu_connector.domain.verification_session import (
    VerificationSession,
    VerificationSessionState,
)
from xianyu_connector.infrastructure.schema import configure_connection
from xianyu_connector.infrastructure.verification_repository_support import (
    encrypt_challenge,
    format_datetime,
    insert_event,
    insert_session,
    parse_datetime,
    session_from_row,
)
from xianyu_connector.security.aes_gcm import EncryptedSecret, SecretCipher


class LocalHandoffConflict(RuntimeError):
    pass


class LocalHandoffGone(RuntimeError):
    pass


class LocalHandoffNotFound(LookupError):
    pass


class InvalidLocalHandoffToken(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LocalHandoffGrant:
    session: VerificationSession
    token: str


class SqliteLocalHandoffRepository:
    def __init__(self, database_path: Path, cipher: SecretCipher) -> None:
        self._database_path = database_path
        self._cipher = cipher

    def create_or_get(
        self,
        account_id: str,
        operator_id: str,
        idempotency_key: str,
        challenge_url: str,
        *,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> LocalHandoffGrant:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key is required")
        current_time = _utc(now or datetime.now(UTC))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_stale_sessions(connection, account_id, current_time)
            existing = self._grant_by_idempotency(connection, account_id, key)
            if existing:
                result = self._existing_grant(
                    connection,
                    existing,
                    operator_id,
                    current_time,
                )
                connection.commit()
                return result
            active = connection.execute(
                "SELECT session_id FROM account_verification_sessions "
                "WHERE account_id = ? AND state IN "
                "('requested','starting','waiting_for_operator','operator_active','submitted','verifying')",
                (account_id,),
            ).fetchone()
            if active:
                connection.rollback()
                raise LocalHandoffConflict("another verification session is active")
            result = self._insert_grant(
                connection,
                account_id,
                operator_id,
                key,
                challenge_url,
                current_time,
                ttl_seconds,
            )
            connection.commit()
            return result

    def get_session(self, account_id: str, session_id: str) -> VerificationSession:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT s.* FROM account_verification_sessions s "
                "JOIN local_verification_handoff_grants g USING (session_id) "
                "WHERE s.session_id = ? AND s.account_id = ?",
                (session_id, account_id),
            ).fetchone()
        if not row:
            raise LocalHandoffNotFound(session_id)
        return session_from_row(self._cipher, row)

    def consume_and_activate(
        self,
        account_id: str,
        session_id: str,
        token: str,
        *,
        completion_ttl_seconds: int = 600,
        now: datetime | None = None,
    ) -> VerificationSession:
        if completion_ttl_seconds <= 0:
            raise ValueError("completion_ttl_seconds must be positive")
        current_time = _utc(now or datetime.now(UTC))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            grant = connection.execute(
                "SELECT * FROM local_verification_handoff_grants "
                "WHERE account_id = ? AND session_id = ?",
                (account_id, session_id),
            ).fetchone()
            session_row = connection.execute(
                "SELECT * FROM account_verification_sessions WHERE session_id = ? AND account_id = ?",
                (session_id, account_id),
            ).fetchone()
            if not grant or not session_row:
                connection.rollback()
                raise LocalHandoffNotFound(session_id)
            if (
                grant["used_at"]
                or grant["revoked_at"]
                or parse_datetime(grant["expires_at"]) <= current_time
            ):
                connection.rollback()
                raise LocalHandoffGone("local handoff grant is no longer available")
            if not secrets.compare_digest(grant["token_hash"], _hash_token(token)):
                connection.rollback()
                raise InvalidLocalHandoffToken("invalid local handoff token")
            session = session_from_row(self._cipher, session_row)
            if session.state is not VerificationSessionState.WAITING_FOR_OPERATOR:
                connection.rollback()
                raise LocalHandoffGone("local handoff grant is no longer available")
            activated = session.transition(
                VerificationSessionState.OPERATOR_ACTIVE,
                now=current_time,
            )
            activated = replace(
                activated,
                expires_at=current_time + timedelta(seconds=completion_ttl_seconds),
            )
            updated = connection.execute(
                "UPDATE local_verification_handoff_grants "
                "SET used_at = ?, token_ciphertext = NULL, token_nonce = NULL, "
                "token_key_version = NULL WHERE grant_id = ? AND used_at IS NULL",
                (format_datetime(current_time), grant["grant_id"]),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise LocalHandoffGone("local handoff grant is no longer available")
            session_updated = connection.execute(
                "UPDATE account_verification_sessions SET state = ?, version = ?, "
                "expires_at = ?, last_activity_at = ? WHERE session_id = ? AND version = ?",
                (
                    activated.state.value,
                    activated.version,
                    format_datetime(activated.expires_at),
                    format_datetime(activated.last_activity_at),
                    session_id,
                    session.version,
                ),
            )
            if session_updated.rowcount != 1:
                connection.rollback()
                raise LocalHandoffGone("local handoff grant is no longer available")
            insert_event(connection, activated, session.state, "local_handoff_consumed")
            connection.commit()
            return activated

    def _expire_stale_sessions(
        self,
        connection: sqlite3.Connection,
        account_id: str,
        now: datetime,
    ) -> None:
        rows = connection.execute(
            "SELECT s.* FROM account_verification_sessions s "
            "JOIN local_verification_handoff_grants g USING (session_id) "
            "WHERE s.account_id = ? AND s.state IN "
            "('requested','starting','waiting_for_operator','operator_active','submitted','verifying') "
            "AND s.expires_at <= ?",
            (account_id, format_datetime(now)),
        ).fetchall()
        for row in rows:
            session = session_from_row(self._cipher, row)
            expired = session.transition(
                VerificationSessionState.EXPIRED,
                now=now,
                reason_code="verification_timeout",
                reason_message="人工验证会话已超时",
            )
            updated = connection.execute(
                "UPDATE account_verification_sessions SET state = ?, version = ?, "
                "reason_code = ?, reason_message = ?, last_activity_at = ?, completed_at = ? "
                "WHERE session_id = ? AND version = ?",
                (
                    expired.state.value,
                    expired.version,
                    expired.reason_code,
                    expired.reason_message,
                    format_datetime(expired.last_activity_at),
                    format_datetime(expired.completed_at),
                    expired.session_id,
                    session.version,
                ),
            )
            if updated.rowcount != 1:
                raise LocalHandoffConflict("verification session changed while expiring")
            connection.execute(
                "UPDATE local_verification_handoff_grants SET revoked_at = ?, "
                "token_ciphertext = NULL, token_nonce = NULL, token_key_version = NULL "
                "WHERE session_id = ? AND used_at IS NULL AND revoked_at IS NULL",
                (format_datetime(now), expired.session_id),
            )
            insert_event(connection, expired, session.state, "expired")

    def revoke(self, session_id: str, *, now: datetime | None = None) -> None:
        revoked_at = format_datetime(_utc(now or datetime.now(UTC)))
        with self._connect() as connection:
            connection.execute(
                "UPDATE local_verification_handoff_grants SET revoked_at = ?, "
                "token_ciphertext = NULL, token_nonce = NULL, token_key_version = NULL "
                "WHERE session_id = ? AND used_at IS NULL AND revoked_at IS NULL",
                (revoked_at, session_id),
            )
            connection.commit()

    def _existing_grant(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        operator_id: str,
        now: datetime,
    ) -> LocalHandoffGrant:
        if row["operator_id"] != operator_id:
            raise LocalHandoffConflict("local handoff belongs to another operator")
        session_row = connection.execute(
            "SELECT * FROM account_verification_sessions WHERE session_id = ?",
            (row["session_id"],),
        ).fetchone()
        if not session_row:
            raise LocalHandoffNotFound(row["session_id"])
        session = session_from_row(self._cipher, session_row)
        if row["used_at"] or row["revoked_at"] or parse_datetime(row["expires_at"]) <= now:
            return LocalHandoffGrant(session, "")
        encrypted = EncryptedSecret(
            row["token_ciphertext"],
            row["token_nonce"],
            row["token_key_version"],
        )
        token = self._cipher.decrypt(
            encrypted,
            associated_data=_token_associated_data(row["grant_id"]),
        )
        return LocalHandoffGrant(session, token)

    def _insert_grant(
        self,
        connection: sqlite3.Connection,
        account_id: str,
        operator_id: str,
        idempotency_key: str,
        challenge_url: str,
        now: datetime,
        ttl_seconds: int,
    ) -> LocalHandoffGrant:
        session_id = uuid.uuid4().hex
        requested = VerificationSessionManager.new_session(
            session_id,
            account_id,
            operator_id,
            now,
            now + timedelta(seconds=ttl_seconds),
            idempotency_key=f"local-handoff:{account_id}:{idempotency_key}",
            challenge_info=challenge_url,
        )
        starting = requested.transition(VerificationSessionState.STARTING, now=now)
        waiting = starting.transition(VerificationSessionState.WAITING_FOR_OPERATOR, now=now)
        insert_session(connection, waiting, encrypt_challenge(self._cipher, waiting))
        insert_event(connection, requested, None, "created")
        insert_event(connection, starting, requested.state, "transition")
        insert_event(connection, waiting, starting.state, "transition")

        grant_id = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        encrypted_token = self._cipher.encrypt(
            token,
            associated_data=_token_associated_data(grant_id),
        )
        connection.execute(
            "INSERT INTO local_verification_handoff_grants "
            "(grant_id, session_id, account_id, operator_id, idempotency_key, token_hash, "
            "token_ciphertext, token_nonce, token_key_version, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                grant_id,
                session_id,
                account_id,
                operator_id,
                idempotency_key,
                _hash_token(token),
                encrypted_token.ciphertext,
                encrypted_token.nonce,
                encrypted_token.key_version,
                format_datetime(now),
                format_datetime(waiting.expires_at),
            ),
        )
        return LocalHandoffGrant(waiting, token)

    @staticmethod
    def _grant_by_idempotency(
        connection: sqlite3.Connection,
        account_id: str,
        idempotency_key: str,
    ) -> sqlite3.Row | None:
        row = connection.execute(
            "SELECT * FROM local_verification_handoff_grants "
            "WHERE account_id = ? AND idempotency_key = ?",
            (account_id, idempotency_key),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        configure_connection(connection)
        return connection


def _hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _token_associated_data(grant_id: str) -> bytes:
    return f"local-verification-handoff:{grant_id}".encode()


def _utc(value: datetime) -> datetime:
    normalized = value if value.tzinfo else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC)
