from __future__ import annotations

import sqlite3
from pathlib import Path


class DatabaseHealthError(RuntimeError):
    pass


class SqliteHealthProbe:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def verify(self) -> None:
        try:
            with sqlite3.connect(self._database_path, timeout=2) as connection:
                row = connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'account_runtime_states'
                    """
                ).fetchone()
        except sqlite3.Error as exc:
            raise DatabaseHealthError("database unavailable") from exc
        if not row:
            raise DatabaseHealthError("connector migration missing")
