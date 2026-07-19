from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from xianyu_connector.domain.account_state import (
    AccountReadiness,
    AccountRuntime,
    AccountState,
)
from xianyu_connector.infrastructure.schema import configure_connection


class RuntimeVersionConflict(RuntimeError):
    pass


class SqliteRuntimeRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def get(self, account_id: str) -> AccountRuntime | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM account_runtime_states WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return _runtime_from_row(row) if row else None

    def list_all(self) -> list[AccountRuntime]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM account_runtime_states ORDER BY account_id"
            ).fetchall()
        return [_runtime_from_row(row) for row in rows]

    def save_initial(self, runtime: AccountRuntime) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(_INSERT_RUNTIME_SQL, _runtime_values(runtime, now))
            connection.commit()

    def ensure(self, account_id: str) -> AccountRuntime:
        existing = self.get(account_id)
        if existing:
            return existing
        runtime = AccountRuntime(account_id=account_id, entered_at=datetime.now(UTC))
        try:
            self.save_initial(runtime)
        except sqlite3.IntegrityError:
            return self.get(account_id) or runtime
        return runtime

    def save_transition(self, previous: AccountRuntime, current: AccountRuntime) -> None:
        if current.version != previous.version + 1:
            raise RuntimeVersionConflict("runtime version must advance exactly once")

        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                _UPDATE_RUNTIME_SQL,
                (*_runtime_update_values(current, now), previous.version, current.account_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeVersionConflict(current.account_id)
            connection.execute(
                """
                INSERT INTO account_runtime_events (
                    account_id, from_state, to_state, reason_code,
                    reason_message, version, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    current.account_id,
                    previous.state.value,
                    current.state.value,
                    current.reason_code,
                    current.reason_message,
                    current.version,
                    current.entered_at.isoformat(),
                ),
            )
            connection.commit()

    def save_observation(self, previous: AccountRuntime, current: AccountRuntime) -> None:
        if current.state is not previous.state or current.version != previous.version:
            raise RuntimeVersionConflict("observation cannot change state or version")
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                _UPDATE_RUNTIME_SQL,
                (*_runtime_update_values(current, now), previous.version, current.account_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeVersionConflict(current.account_id)
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        configure_connection(connection)
        return connection


def _runtime_from_row(row: sqlite3.Row) -> AccountRuntime:
    readiness = AccountReadiness(
        bool(row["session_ready"]),
        bool(row["token_ready"]),
        bool(row["websocket_ready"]),
        bool(row["stream_ready"]),
    )
    return AccountRuntime(
        account_id=row["account_id"],
        state=AccountState(row["state"]),
        readiness=readiness,
        version=row["version"],
        reason_code=row["reason_code"],
        reason_message=row["reason_message"],
        entered_at=_parse_datetime(row["entered_at"]) or datetime.min.replace(tzinfo=UTC),
        last_heartbeat_ack_at=_parse_datetime(row["last_heartbeat_ack_at"]),
        last_session_keepalive_at=_parse_datetime(row["last_session_keepalive_at"]),
        last_business_message_at=_parse_datetime(row["last_business_message_at"]),
        next_action_at=_parse_datetime(row["next_action_at"]),
        worker_heartbeat_at=_parse_datetime(row["worker_heartbeat_at"]),
        worker_pid=row["worker_pid"],
        profile_generation=row["profile_generation"],
        restart_count=row["restart_count"],
    )


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _runtime_values(runtime: AccountRuntime, updated_at: str) -> tuple[object, ...]:
    return (runtime.account_id, *_runtime_update_values(runtime, updated_at))


def _runtime_update_values(runtime: AccountRuntime, updated_at: str) -> tuple[object, ...]:
    readiness = runtime.readiness
    return (
        runtime.state.value,
        runtime.version,
        int(readiness.session_ready),
        int(readiness.token_ready),
        int(readiness.websocket_ready),
        int(readiness.stream_ready),
        runtime.reason_code,
        runtime.reason_message,
        runtime.entered_at.isoformat(),
        _format_datetime(runtime.last_heartbeat_ack_at),
        _format_datetime(runtime.last_session_keepalive_at),
        _format_datetime(runtime.last_business_message_at),
        _format_datetime(runtime.next_action_at),
        _format_datetime(runtime.worker_heartbeat_at),
        runtime.worker_pid,
        runtime.profile_generation,
        runtime.restart_count,
        updated_at,
    )


def _format_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


_RUNTIME_COLUMNS = """
state, version, session_ready, token_ready, websocket_ready, stream_ready,
reason_code, reason_message, entered_at, last_heartbeat_ack_at,
last_session_keepalive_at, last_business_message_at, next_action_at,
worker_heartbeat_at, worker_pid, profile_generation, restart_count, updated_at
"""
_INSERT_RUNTIME_SQL = f"""
INSERT INTO account_runtime_states (account_id, {_RUNTIME_COLUMNS})
VALUES ({", ".join("?" for _ in range(19))})
"""
_UPDATE_RUNTIME_SQL = f"""
UPDATE account_runtime_states SET
{", ".join(f"{column.strip()} = ?" for column in _RUNTIME_COLUMNS.split(","))}
WHERE version = ? AND account_id = ?
"""
