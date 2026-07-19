from __future__ import annotations

import sqlite3

CONNECTOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_runtime_states (
    account_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    version INTEGER NOT NULL,
    session_ready INTEGER NOT NULL DEFAULT 0,
    token_ready INTEGER NOT NULL DEFAULT 0,
    websocket_ready INTEGER NOT NULL DEFAULT 0,
    stream_ready INTEGER NOT NULL DEFAULT 0,
    reason_code TEXT,
    reason_message TEXT,
    entered_at TEXT NOT NULL,
    last_heartbeat_ack_at TEXT,
    last_session_keepalive_at TEXT,
    last_business_message_at TEXT,
    next_action_at TEXT,
    worker_heartbeat_at TEXT,
    worker_pid INTEGER,
    profile_generation INTEGER NOT NULL DEFAULT 1,
    restart_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_runtime_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    reason_code TEXT,
    reason_message TEXT,
    version INTEGER NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runtime_events_account_time
ON account_runtime_events(account_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS account_commands (
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
    UNIQUE(account_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_account_commands_queue
ON account_commands(status, created_at);

CREATE TABLE IF NOT EXISTS account_secrets (
    account_id TEXT NOT NULL,
    secret_type TEXT NOT NULL,
    ciphertext BLOB NOT NULL,
    nonce BLOB NOT NULL,
    key_version INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(account_id, secret_type)
);

"""

VERIFICATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_verification_sessions (
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
    UNIQUE(account_id, operator_id, idempotency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_verification_active_account
ON account_verification_sessions(account_id)
WHERE state IN ('requested', 'starting', 'waiting_for_operator', 'operator_active', 'submitted', 'verifying');
CREATE TABLE IF NOT EXISTS account_verification_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    event_type TEXT NOT NULL,
    reason_code TEXT,
    reason_message TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verification_events_session_time
ON account_verification_events(session_id, occurred_at DESC);
CREATE TABLE IF NOT EXISTS verification_access_tokens (
    token_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    token_hash BLOB NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_verification_tokens_session
ON verification_access_tokens(session_id);
"""

LOCAL_VERIFICATION_HANDOFF_SCHEMA = """
CREATE TABLE IF NOT EXISTS local_verification_handoff_grants (
    grant_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    token_hash BLOB NOT NULL UNIQUE,
    token_ciphertext BLOB,
    token_nonce BLOB,
    token_key_version INTEGER,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    revoked_at TEXT,
    UNIQUE(account_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_local_handoff_grants_account
ON local_verification_handoff_grants(account_id, created_at DESC);
"""


def configure_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")


def apply_connector_schema(connection: sqlite3.Connection) -> None:
    configure_connection(connection)
    connection.executescript(CONNECTOR_SCHEMA)
    connection.executescript(VERIFICATION_SCHEMA)
    connection.executescript(LOCAL_VERIFICATION_HANDOFF_SCHEMA)
    connection.commit()
