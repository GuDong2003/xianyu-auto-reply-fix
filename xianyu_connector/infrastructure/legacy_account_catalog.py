from __future__ import annotations

import sqlite3
from pathlib import Path

from xianyu_connector.infrastructure.schema import configure_connection


class LegacyAccountCatalog:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def enabled_account_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT cookies.id FROM cookies
                LEFT JOIN cookie_status ON cookie_status.cookie_id = cookies.id
                WHERE COALESCE(cookie_status.enabled, 1) = 1
                ORDER BY cookies.id
                """
            ).fetchall()
        return [str(row[0]) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=5)
        configure_connection(connection)
        return connection
