from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from xianyu_connector.infrastructure.schema import configure_connection
from xianyu_connector.security.aes_gcm import EncryptedSecret, SecretCipher


class SqliteSecretRepository:
    def __init__(self, database_path: Path, cipher: SecretCipher) -> None:
        self._database_path = database_path
        self._cipher = cipher

    def save(self, account_id: str, secret_type: str, plaintext: str) -> None:
        associated_data = _associated_data(account_id, secret_type)
        secret = self._cipher.encrypt(plaintext, associated_data=associated_data)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO account_secrets (
                    account_id, secret_type, ciphertext, nonce, key_version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, secret_type) DO UPDATE SET
                    ciphertext = excluded.ciphertext,
                    nonce = excluded.nonce,
                    key_version = excluded.key_version,
                    updated_at = excluded.updated_at
                """,
                (
                    account_id,
                    secret_type,
                    secret.ciphertext,
                    secret.nonce,
                    secret.key_version,
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.commit()

    def get(self, account_id: str, secret_type: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT ciphertext, nonce, key_version FROM account_secrets
                WHERE account_id = ? AND secret_type = ?
                """,
                (account_id, secret_type),
            ).fetchone()
        if not row:
            return None
        secret = EncryptedSecret(row["ciphertext"], row["nonce"], row["key_version"])
        return self._cipher.decrypt(
            secret, associated_data=_associated_data(account_id, secret_type)
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        configure_connection(connection)
        return connection


def _associated_data(account_id: str, secret_type: str) -> bytes:
    return f"{account_id}:{secret_type}".encode()
