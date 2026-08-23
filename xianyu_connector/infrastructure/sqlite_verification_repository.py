from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Protocol

from xianyu_connector.domain.verification_session import (
    VerificationEvent,
    VerificationSession,
)
from xianyu_connector.infrastructure.schema import configure_connection
from xianyu_connector.infrastructure.verification_repository_support import (
    encrypt_challenge,
    event_from_row,
    format_datetime,
    insert_event,
    insert_session,
    session_from_row,
)
from xianyu_connector.security.aes_gcm import SecretCipher


class VerificationVersionConflict(RuntimeError):
    pass


class VerificationRepository(Protocol):
    """Persistence port for verification sessions and their short-lived access grants."""

    def create(self, session: VerificationSession) -> None: ...

    def get(self, session_id: str) -> VerificationSession | None: ...

    def get_by_idempotency(
        self,
        account_id: str,
        operator_id: str,
        idempotency_key: str,
    ) -> VerificationSession | None: ...

    def get_active_for_account(self, account_id: str) -> VerificationSession | None: ...

    def save_transition(self, previous: VerificationSession, current: VerificationSession) -> None: ...

    def touch(self, session: VerificationSession) -> VerificationSession: ...

    def list_active(self) -> list[VerificationSession]: ...

    def events(self, session_id: str) -> list[VerificationEvent]: ...

    def create_access_token(
        self,
        session_id: str,
        token_hash: bytes,
        created_at: datetime,
        expires_at: datetime,
    ) -> str: ...

    def consume_access_token(self, session_id: str, token_hash: bytes, now: datetime) -> bool: ...

    def create_with_access_token(
        self,
        session: VerificationSession,
        token_hash: bytes,
        created_at: datetime,
    ) -> str: ...


class SqliteVerificationRepository:
    def __init__(self, database_path: Path, cipher: SecretCipher) -> None:
        self._database_path = database_path
        self._cipher = cipher

    def create(self, session: VerificationSession) -> None:
        encrypted = encrypt_challenge(self._cipher, session)
        with self._connect() as connection:
            insert_session(connection, session, encrypted)
            insert_event(connection, session, None, "created")
            connection.commit()

    def get(self, session_id: str) -> VerificationSession | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM account_verification_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return session_from_row(self._cipher, row) if row else None

    def get_by_idempotency(
        self,
        account_id: str,
        operator_id: str,
        idempotency_key: str,
    ) -> VerificationSession | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM account_verification_sessions "
                "WHERE account_id = ? AND operator_id = ? AND idempotency_key = ?",
                (account_id, operator_id, idempotency_key),
            ).fetchone()
        return session_from_row(self._cipher, row) if row else None

    def get_active_for_account(self, account_id: str) -> VerificationSession | None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM account_verification_sessions WHERE account_id = ? "
                "AND state IN ('requested','starting','waiting_for_operator','operator_active',"
                "'submitted','verifying') ORDER BY created_at DESC LIMIT 1",
                (account_id,),
            ).fetchall()
        return session_from_row(self._cipher, rows[0]) if rows else None

    def save_transition(self, previous: VerificationSession, current: VerificationSession) -> None:
        if current.version != previous.version + 1:
            raise VerificationVersionConflict("verification version must advance exactly once")
        stored_idempotency_key = (
            _retired_idempotency_key(current)
            if current.terminal
            else current.idempotency_key
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE account_verification_sessions SET state = ?, version = ?,
                    reason_code = ?, reason_message = ?, worker_pid = ?,
                    last_activity_at = ?, completed_at = ?, idempotency_key = ?
                WHERE session_id = ? AND version = ?
                """,
                (
                    current.state.value,
                    current.version,
                    current.reason_code,
                    current.reason_message,
                    current.worker_pid,
                    format_datetime(current.last_activity_at),
                    format_datetime(current.completed_at),
                    stored_idempotency_key,
                    current.session_id,
                    previous.version,
                ),
            )
            if cursor.rowcount != 1:
                raise VerificationVersionConflict(current.session_id)
            insert_event(connection, current, previous.state, "transition")
            connection.commit()

    def touch(self, session: VerificationSession) -> VerificationSession:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE account_verification_sessions SET last_activity_at = ? "
                "WHERE session_id = ? AND version = ?",
                (format_datetime(session.last_activity_at), session.session_id, session.version),
            )
            if cursor.rowcount != 1:
                raise VerificationVersionConflict(session.session_id)
            connection.commit()
        return session

    def list_active(self) -> list[VerificationSession]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM account_verification_sessions WHERE state IN "
                "('requested','starting','waiting_for_operator','operator_active','submitted','verifying')"
            ).fetchall()
        return [session_from_row(self._cipher, row) for row in rows]

    def events(self, session_id: str) -> list[VerificationEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM account_verification_events WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [event_from_row(row) for row in rows]

    def create_access_token(
        self,
        session_id: str,
        token_hash: bytes,
        created_at: datetime,
        expires_at: datetime,
    ) -> str:
        token_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO verification_access_tokens "
                "(token_id, session_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (token_id, session_id, token_hash, format_datetime(created_at), format_datetime(expires_at)),
            )
            connection.commit()
        return token_id

    def create_with_access_token(
        self,
        session: VerificationSession,
        token_hash: bytes,
        created_at: datetime,
    ) -> str:
        token_id = uuid.uuid4().hex
        encrypted = encrypt_challenge(self._cipher, session)
        with self._connect() as connection:
            insert_session(connection, session, encrypted)
            insert_event(connection, session, None, "created")
            connection.execute(
                "INSERT INTO verification_access_tokens "
                "(token_id, session_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (
                    token_id,
                    session.session_id,
                    token_hash,
                    format_datetime(created_at),
                    format_datetime(session.expires_at),
                ),
            )
            connection.commit()
        return token_id

    def consume_access_token(self, session_id: str, token_hash: bytes, now: datetime) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE verification_access_tokens SET used_at = ?
                WHERE session_id = ? AND token_hash = ? AND used_at IS NULL
                  AND revoked_at IS NULL AND expires_at > ?
                """,
                (format_datetime(now), session_id, token_hash, format_datetime(now)),
            )
            connection.commit()
        return cursor.rowcount == 1

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        configure_connection(connection)
        return connection


def _retired_idempotency_key(session: VerificationSession) -> str:
    return f"{session.idempotency_key}:retired:{session.session_id}"
