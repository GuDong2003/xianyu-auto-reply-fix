from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from xianyu_connector.domain.verification_session import (
    VerificationEvent,
    VerificationSession,
    VerificationSessionState,
)
from xianyu_connector.security.aes_gcm import EncryptedSecret, SecretCipher


def challenge_associated_data(session_id: str) -> bytes:
    return f"verification-challenge:{session_id}".encode()


def format_datetime(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def parse_datetime(value: str | None) -> datetime:
    parsed = datetime.fromisoformat(value) if value else datetime.min.replace(tzinfo=UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def insert_event(
    connection: sqlite3.Connection,
    session: VerificationSession,
    from_state: VerificationSessionState | None,
    event_type: str,
) -> None:
    connection.execute(
        """
        INSERT INTO account_verification_events (
            session_id, account_id, from_state, to_state, event_type,
            reason_code, reason_message, metadata_json, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session.session_id,
            session.account_id,
            from_state.value if from_state else None,
            session.state.value,
            event_type,
            session.reason_code,
            session.reason_message,
            json.dumps({}, ensure_ascii=True),
            format_datetime(session.last_activity_at),
        ),
    )


def insert_session(
    connection: sqlite3.Connection,
    session: VerificationSession,
    encrypted: EncryptedSecret | None,
) -> None:
    connection.execute(
        """
        INSERT INTO account_verification_sessions (
            session_id, account_id, operator_id, idempotency_key, state, version,
            reason_code, reason_message, challenge_ciphertext, challenge_nonce,
            challenge_key_version, worker_pid, created_at, expires_at,
            last_activity_at, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session.session_id,
            session.account_id,
            session.operator_id,
            session.idempotency_key,
            session.state.value,
            session.version,
            session.reason_code,
            session.reason_message,
            encrypted.ciphertext if encrypted else None,
            encrypted.nonce if encrypted else None,
            encrypted.key_version if encrypted else None,
            session.worker_pid,
            format_datetime(session.created_at),
            format_datetime(session.expires_at),
            format_datetime(session.last_activity_at),
            format_datetime(session.completed_at),
        ),
    )


def event_from_row(row: sqlite3.Row) -> VerificationEvent:
    return VerificationEvent(
        session_id=row["session_id"],
        account_id=row["account_id"],
        from_state=VerificationSessionState(row["from_state"]) if row["from_state"] else None,
        to_state=VerificationSessionState(row["to_state"]),
        event_type=row["event_type"],
        occurred_at=parse_datetime(row["occurred_at"]),
        reason_code=row["reason_code"],
        reason_message=row["reason_message"],
    )


def encrypt_challenge(
    cipher: SecretCipher,
    session: VerificationSession,
) -> EncryptedSecret | None:
    if session.challenge_info is None:
        return None
    return cipher.encrypt(
        session.challenge_info,
        associated_data=challenge_associated_data(session.session_id),
    )


def session_from_row(cipher: SecretCipher, row: sqlite3.Row) -> VerificationSession:
    challenge = None
    if row["challenge_ciphertext"] is not None:
        challenge = cipher.decrypt(
            EncryptedSecret(
                row["challenge_ciphertext"],
                row["challenge_nonce"],
                row["challenge_key_version"],
            ),
            associated_data=challenge_associated_data(row["session_id"]),
        )
    return VerificationSession(
        session_id=row["session_id"],
        account_id=row["account_id"],
        operator_id=row["operator_id"],
        state=VerificationSessionState(row["state"]),
        created_at=parse_datetime(row["created_at"]),
        expires_at=parse_datetime(row["expires_at"]),
        last_activity_at=parse_datetime(row["last_activity_at"]),
        idempotency_key=row["idempotency_key"],
        version=row["version"],
        reason_code=row["reason_code"],
        reason_message=row["reason_message"],
        worker_pid=row["worker_pid"],
        completed_at=parse_datetime(row["completed_at"]),
        challenge_info=challenge,
    )
