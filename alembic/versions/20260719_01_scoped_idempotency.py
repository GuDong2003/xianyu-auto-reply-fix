"""Scope connector idempotency keys without losing existing rows."""

from __future__ import annotations

from alembic import op

revision = "20260719_01"
down_revision = "20260718_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _rebuild_commands("UNIQUE(account_id, idempotency_key)")
    _rebuild_verification(
        "UNIQUE(account_id, operator_id, idempotency_key)",
    )


def downgrade() -> None:
    _rebuild_commands("UNIQUE(idempotency_key)")
    _rebuild_verification("UNIQUE(idempotency_key)")


def _rebuild_commands(unique_constraint: str) -> None:
    op.execute(
        f"""
        CREATE TABLE account_commands_replacement (
            command_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            command TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            {unique_constraint}
        )
        """
    )
    op.execute(
        """
        INSERT INTO account_commands_replacement (
            command_id, account_id, command, idempotency_key, payload_json, status,
            result_json, error_message, created_at, started_at, completed_at
        )
        SELECT command_id, account_id, command, idempotency_key, payload_json, status,
               result_json, error_message, created_at, started_at, completed_at
        FROM account_commands
        """
    )
    op.execute("DROP TABLE account_commands")
    op.execute("ALTER TABLE account_commands_replacement RENAME TO account_commands")
    op.execute(
        "CREATE INDEX idx_account_commands_queue ON account_commands(status, created_at)"
    )


def _rebuild_verification(unique_constraint: str) -> None:
    op.execute(
        f"""
        CREATE TABLE account_verification_sessions_replacement (
            session_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            operator_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            state TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 0,
            reason_code TEXT,
            reason_message TEXT,
            challenge_ciphertext BLOB,
            challenge_nonce BLOB,
            challenge_key_version INTEGER,
            worker_pid INTEGER,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_activity_at TEXT NOT NULL,
            completed_at TEXT,
            {unique_constraint}
        )
        """
    )
    op.execute(
        """
        INSERT INTO account_verification_sessions_replacement (
            session_id, account_id, operator_id, idempotency_key, state, version,
            reason_code, reason_message, challenge_ciphertext, challenge_nonce,
            challenge_key_version, worker_pid, created_at, expires_at, last_activity_at,
            completed_at
        )
        SELECT session_id, account_id, operator_id, idempotency_key, state, version,
               reason_code, reason_message, challenge_ciphertext, challenge_nonce,
               challenge_key_version, worker_pid, created_at, expires_at, last_activity_at,
               completed_at
        FROM account_verification_sessions
        """
    )
    op.execute("DROP TABLE account_verification_sessions")
    op.execute(
        "ALTER TABLE account_verification_sessions_replacement "
        "RENAME TO account_verification_sessions"
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_verification_active_account "
        "ON account_verification_sessions(account_id) "
        "WHERE state IN ('requested', 'starting', 'waiting_for_operator', "
        "'operator_active', 'submitted', 'verifying')"
    )
