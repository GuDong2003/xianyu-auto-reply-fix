from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command


def test_upgrade_persists_connector_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "connector.db"
    config = Config(repository_root / "alembic.ini")
    config.set_main_option("script_location", str(repository_root / "alembic"))
    monkeypatch.setenv("DB_PATH", str(database_path))

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert revision == ("20260719_02",)
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(local_verification_handoff_grants)"
            )
        }
    assert {
        "session_id",
        "account_id",
        "operator_id",
        "idempotency_key",
        "token_hash",
        "token_ciphertext",
        "used_at",
    } <= columns


def test_scoped_idempotency_revision_preserves_rows_and_isolates_accounts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "connector.db"
    config = Config(repository_root / "alembic.ini")
    config.set_main_option("script_location", str(repository_root / "alembic"))
    monkeypatch.setenv("DB_PATH", str(database_path))
    command.upgrade(config, "20260718_02")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO account_commands "
            "(command_id, account_id, command, idempotency_key, payload_json, status, created_at) "
            "VALUES ('command-1', 'account-1', 'start', 'shared-key', '{}', 'queued', '2026-07-19T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO account_verification_sessions "
            "(session_id, account_id, operator_id, idempotency_key, state, version, created_at, "
            "expires_at, last_activity_at) VALUES "
            "('session-1', 'account-1', '7', 'shared-key', 'failed', 1, "
            "'2026-07-19T00:00:00+00:00', '2026-07-19T00:10:00+00:00', "
            "'2026-07-19T00:00:00+00:00')"
        )
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT account_id, idempotency_key FROM account_commands WHERE command_id = 'command-1'"
        ).fetchone() == ("account-1", "shared-key")
        assert connection.execute(
            "SELECT account_id, operator_id, idempotency_key "
            "FROM account_verification_sessions WHERE session_id = 'session-1'"
        ).fetchone() == ("account-1", "7", "shared-key:retired:session-1")
        connection.execute(
            "INSERT INTO account_commands "
            "(command_id, account_id, command, idempotency_key, payload_json, status, created_at) "
            "VALUES ('command-2', 'account-2', 'start', 'shared-key', '{}', 'queued', '2026-07-19T00:00:01+00:00')"
        )
        connection.execute(
            "INSERT INTO account_verification_sessions "
            "(session_id, account_id, operator_id, idempotency_key, state, version, created_at, "
            "expires_at, last_activity_at) VALUES "
            "('session-2', 'account-2', '7', 'shared-key', 'failed', 1, "
            "'2026-07-19T00:00:01+00:00', '2026-07-19T00:10:01+00:00', "
            "'2026-07-19T00:00:01+00:00')"
        )
        connection.execute(
            "INSERT INTO account_verification_sessions "
            "(session_id, account_id, operator_id, idempotency_key, state, version, created_at, "
            "expires_at, last_activity_at) VALUES "
            "('session-3', 'account-1', '7', 'shared-key', 'requested', 0, "
            "'2026-07-19T00:00:02+00:00', '2026-07-19T00:10:02+00:00', "
            "'2026-07-19T00:00:02+00:00')"
        )


def test_local_handoff_revision_downgrades_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "connector.db"
    config = Config(repository_root / "alembic.ini")
    config.set_main_option("script_location", str(repository_root / "alembic"))
    monkeypatch.setenv("DB_PATH", str(database_path))

    command.upgrade(config, "head")
    command.downgrade(config, "20260718_01")

    with sqlite3.connect(database_path) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'local_verification_handoff_grants'"
        ).fetchone()
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert table is None
    assert revision == ("20260718_01",)
