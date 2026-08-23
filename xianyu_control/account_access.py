from __future__ import annotations

import sqlite3
from pathlib import Path

from xianyu_connector.infrastructure.schema import configure_connection


class AccountAccessRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def user_can_access(self, account_id: str, user_id: int, *, is_admin: bool) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id FROM cookies WHERE id = ?",
                (account_id,),
            ).fetchone()
        return bool(row and (is_admin or int(row[0]) == user_id))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=5)
        configure_connection(connection)
        return connection
