from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from xianyu_connector.domain.commands import (
    AccountCommand,
    CommandRecord,
    CommandStatus,
)
from xianyu_connector.infrastructure.schema import configure_connection


class SqliteCommandRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def enqueue(
        self,
        account_id: str,
        command: AccountCommand,
        idempotency_key: str,
        payload: dict[str, object],
    ) -> CommandRecord:
        existing = self._get_by_idempotency_key(account_id, idempotency_key)
        if existing:
            return existing

        record = CommandRecord(
            command_id=str(uuid.uuid4()),
            account_id=account_id,
            command=command,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO account_commands (
                        command_id, account_id, command, idempotency_key,
                        payload_json, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.command_id,
                        account_id,
                        command.value,
                        idempotency_key,
                        json.dumps(payload, ensure_ascii=True, sort_keys=True),
                        CommandStatus.QUEUED.value,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError:
                return self._get_by_idempotency_key(account_id, idempotency_key) or record
        return record

    def claim_next(self) -> CommandRecord | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM account_commands
                WHERE status = ? ORDER BY created_at LIMIT 1
                """,
                (CommandStatus.QUEUED.value,),
            ).fetchone()
            if not row:
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE account_commands SET status = ?, started_at = ?
                WHERE command_id = ?
                """,
                (CommandStatus.RUNNING.value, datetime.now(UTC).isoformat(), row["command_id"]),
            )
            connection.commit()
        return _command_from_row(row, status=CommandStatus.RUNNING)

    def get(self, command_id: str) -> CommandRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM account_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        return _command_from_row(row) if row else None

    def complete(
        self,
        command_id: str,
        *,
        result: dict[str, object] | None = None,
        error_message: str | None = None,
    ) -> bool:
        status = CommandStatus.FAILED if error_message else CommandStatus.SUCCEEDED
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE account_commands SET status = ?, result_json = ?,
                    error_message = ?, completed_at = ?
                WHERE command_id = ? AND status = ?
                """,
                (
                    status.value,
                    json.dumps(result, ensure_ascii=True, sort_keys=True) if result else None,
                    error_message,
                    datetime.now(UTC).isoformat(),
                    command_id,
                    CommandStatus.RUNNING.value,
                ),
            )
            connection.commit()
            return cursor.rowcount == 1

    def cancel(self, command_id: str, *, error_message: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE account_commands SET status = ?, error_message = ?, completed_at = ?
                WHERE command_id = ? AND status IN (?, ?)
                """,
                (
                    CommandStatus.FAILED.value,
                    error_message,
                    datetime.now(UTC).isoformat(),
                    command_id,
                    CommandStatus.QUEUED.value,
                    CommandStatus.RUNNING.value,
                ),
            )
            connection.commit()
            return cursor.rowcount == 1

    def recover_interrupted(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE account_commands
                SET status = ?, started_at = NULL
                WHERE status = ?
                """,
                (CommandStatus.QUEUED.value, CommandStatus.RUNNING.value),
            )
            connection.commit()
            return cursor.rowcount

    def _get_by_idempotency_key(self, account_id: str, key: str) -> CommandRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM account_commands WHERE account_id = ? AND idempotency_key = ?",
                (account_id, key),
            ).fetchone()
        return _command_from_row(row) if row else None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        configure_connection(connection)
        return connection


def _command_from_row(
    row: sqlite3.Row,
    *,
    status: CommandStatus | None = None,
) -> CommandRecord:
    return CommandRecord(
        command_id=row["command_id"],
        account_id=row["account_id"],
        command=AccountCommand(row["command"]),
        idempotency_key=row["idempotency_key"],
        payload=json.loads(row["payload_json"]),
        status=status or CommandStatus(row["status"]),
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        error_message=row["error_message"],
    )
