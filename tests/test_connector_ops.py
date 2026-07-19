from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

from xianyu_connector.log_archive_sanitizer import sanitize_log_directory
from xianyu_connector.ops import _backup, _prune, _restore_check, _sanitize_logs


def test_backup_and_restore_check_use_real_sqlite_copy(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "data" / "xianyu.db"
    backup_path = tmp_path / "backups"
    database_path.parent.mkdir()
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE cookies (id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO cookies (id) VALUES ('account-1')")
    monkeypatch.setenv("DB_PATH", str(database_path))
    monkeypatch.setenv("XIANYU_BACKUP_DIR", str(backup_path))

    assert _backup() == 0
    assert _restore_check() == 0
    assert not (database_path.parent / "restore-check.db").exists()


def test_backup_pruning_keeps_requested_count(tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / f"xianyu-daily-{index}.db").touch()

    _prune(tmp_path, "xianyu-daily-*.db", keep=2)

    assert len(list(tmp_path.glob("xianyu-daily-*.db"))) == 2


def test_log_sanitizer_redacts_text_and_zip_archives(tmp_path: Path) -> None:
    plain_log = tmp_path / "current.log"
    html_log = tmp_path / "browser.html"
    archive = tmp_path / "previous.log.zip"
    plain_log.write_text("cookie2=plain-secret; token=plain-token", encoding="utf-8")
    html_log.write_text(
        '<a href="https://example.test/?token=html-secret">link</a>',
        encoding="utf-8",
    )
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("previous.log", "password=archive-secret")

    result = sanitize_log_directory(tmp_path)

    assert result.text_files == 2
    assert result.zip_archives == 1
    assert "plain-secret" not in plain_log.read_text(encoding="utf-8")
    assert "html-secret" not in html_log.read_text(encoding="utf-8")
    with zipfile.ZipFile(archive) as source:
        assert b"archive-secret" not in source.read("previous.log")


def test_log_sanitizer_does_not_replace_excluded_active_log(tmp_path: Path) -> None:
    active_log = tmp_path / "active.log"
    archived_log = tmp_path / "archived.log"
    active_log.write_text("token=active-secret", encoding="utf-8")
    archived_log.write_text("token=archived-secret", encoding="utf-8")

    result = sanitize_log_directory(tmp_path, excluded_names={active_log.name})

    assert result.text_files == 1
    assert "active-secret" in active_log.read_text(encoding="utf-8")
    assert "archived-secret" not in archived_log.read_text(encoding="utf-8")


def test_sanitize_logs_command_honors_environment_exclusions(
    tmp_path: Path, monkeypatch
) -> None:
    active_log = tmp_path / "active.log"
    active_log.write_text("token=active-secret", encoding="utf-8")
    monkeypatch.setenv("XIANYU_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("XIANYU_LOG_EXCLUDE", active_log.name)

    assert _sanitize_logs() == 0
    assert "active-secret" in active_log.read_text(encoding="utf-8")
