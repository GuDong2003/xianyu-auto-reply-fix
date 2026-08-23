from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from xianyu_connector.log_archive_sanitizer import sanitize_log_directory


def main() -> int:
    if sys.argv[1:] == ["backup"]:
        return _backup()
    if sys.argv[1:] == ["restore-check"]:
        return _restore_check()
    if sys.argv[1:] == ["sanitize-logs"]:
        return _sanitize_logs()
    print(
        "usage: python -m xianyu_connector.ops backup|restore-check|sanitize-logs",
        file=sys.stderr,
    )
    return 2


def _backup() -> int:
    source = Path(os.getenv("DB_PATH", "/app/data/xianyu_data.db"))
    backup_directory = Path(os.getenv("XIANYU_BACKUP_DIR", "/app/backups"))
    backup_directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    target = backup_directory / f"xianyu-daily-{timestamp}.db"
    with (
        sqlite3.connect(source) as source_connection,
        sqlite3.connect(target) as target_connection,
    ):
        source_connection.backup(target_connection)
    os.chmod(target, 0o600)
    if now.weekday() == 6:
        weekly = backup_directory / f"xianyu-weekly-{now.strftime('%G-W%V')}.db"
        shutil.copy2(target, weekly)
        os.chmod(weekly, 0o600)
    _prune(backup_directory, "xianyu-daily-*.db", keep=14)
    _prune(backup_directory, "xianyu-weekly-*.db", keep=8)
    print(target)
    return 0


def _restore_check() -> int:
    source = Path(os.getenv("DB_PATH", "/app/data/xianyu_data.db"))
    backup_directory = Path(os.getenv("XIANYU_BACKUP_DIR", "/app/backups"))
    backups = sorted(backup_directory.glob("xianyu-daily-*.db"), reverse=True)
    if not backups:
        raise FileNotFoundError("no daily backup is available for restore check")
    restored = source.with_name("restore-check.db")
    shutil.copy2(backups[0], restored)
    try:
        with sqlite3.connect(restored) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            cookies_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cookies'"
            ).fetchone()
        if not integrity or integrity[0] != "ok" or not cookies_table:
            raise RuntimeError("restored backup failed integrity or schema validation")
    finally:
        restored.unlink(missing_ok=True)
    print(backups[0])
    return 0


def _sanitize_logs() -> int:
    log_directory = Path(os.getenv("XIANYU_LOG_DIR", "/app/logs"))
    excluded_names = {
        name.strip()
        for name in os.getenv("XIANYU_LOG_EXCLUDE", "").split(",")
        if name.strip()
    }
    result = sanitize_log_directory(log_directory, excluded_names=excluded_names)
    print(json.dumps({"text_files": result.text_files, "zip_archives": result.zip_archives}))
    return 0


def _prune(directory: Path, pattern: str, *, keep: int) -> None:
    backups = sorted(directory.glob(pattern), reverse=True)
    for stale in backups[keep:]:
        stale.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
