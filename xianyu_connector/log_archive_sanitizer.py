from __future__ import annotations

import json
import os
import zipfile
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from xianyu_connector.security.redaction import redact_text

_TEXT_SUFFIXES = frozenset({".htm", ".html", ".json", ".jsonl", ".log", ".txt"})


@dataclass(frozen=True, slots=True)
class LogSanitizationResult:
    text_files: int = 0
    zip_archives: int = 0


def sanitize_log_directory(
    directory: Path, *, excluded_names: Collection[str] = ()
) -> LogSanitizationResult:
    if not directory.exists():
        return LogSanitizationResult()
    exclusions = frozenset(excluded_names)
    text_files = 0
    zip_archives = 0
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name in exclusions:
            continue
        if path.suffix.lower() == ".zip" and _sanitize_zip(path):
            zip_archives += 1
        elif path.suffix.lower() in _TEXT_SUFFIXES and _sanitize_text(path):
            text_files += 1
    return LogSanitizationResult(text_files, zip_archives)


def _sanitize_text(path: Path) -> bool:
    original = path.read_text(encoding="utf-8", errors="replace")
    sanitized = redact_text(original)
    if sanitized == original:
        return False
    _validate_json_structure(path, original, sanitized)
    temporary = path.with_name(f".{path.name}.sanitize.tmp")
    temporary.write_text(sanitized, encoding="utf-8")
    _replace_preserving_mode(path, temporary)
    return True


def _sanitize_zip(path: Path) -> bool:
    temporary = path.with_name(f".{path.name}.sanitize.tmp")
    changed = False
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(temporary, "w") as target:
        for member in source.infolist():
            payload = source.read(member)
            if Path(member.filename).suffix.lower() in _TEXT_SUFFIXES:
                text = payload.decode("utf-8", errors="replace")
                sanitized = redact_text(text)
                changed = changed or sanitized != text
                payload = sanitized.encode("utf-8")
            target.writestr(member, payload)
    if not changed:
        temporary.unlink()
        return False
    _replace_preserving_mode(path, temporary)
    return True


def _replace_preserving_mode(target: Path, temporary: Path) -> None:
    os.chmod(temporary, target.stat().st_mode & 0o777)
    temporary.replace(target)


def _validate_json_structure(path: Path, original: str, sanitized: str) -> None:
    if path.suffix.lower() != ".json":
        return
    try:
        json.loads(original)
    except json.JSONDecodeError:
        return
    try:
        json.loads(sanitized)
    except json.JSONDecodeError as exc:
        raise ValueError(f"redaction would corrupt JSON: {path}") from exc
