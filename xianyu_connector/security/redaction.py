from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "cookies",
        "password",
        "proxy_pass",
        "token",
        "access_token",
        "refresh_token",
        "x5secdata",
    }
)
_COOKIE_PATTERN = re.compile(r"(?i)(?<![\w.-])([\w.-]+)=([^;\s'\",}]+)")
_KNOWN_COOKIE_PATTERN = re.compile(
    r"(?i)(?<![\w.-])"
    r"(cookie2|_m_h5_tk(?:_enc)?|x5sec(?:data)?|sgcookie|tfstk|cna|unb)="
    r"([^;\s'\",}]+)"
)
_MAPPING_SECRET_PATTERN = re.compile(
    r"(?i)(['\"]?(?:authorization|cookie\w*|password|proxy_pass|token\w*|x5secdata)['\"]?\s*[=:]\s*)"
    r"(['\"]?)([^'\",};\s]+)(['\"]?)"
)
_AUTH_PATTERN = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/-]+=*")
_QUERY_SECRET_PATTERN = re.compile(
    r'''(?i)(x5secdata|token|access_token|auth|sign)=([^&\s'",};<>\]\)]+)'''
)


def redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(text: str) -> str:
    redacted = _AUTH_PATTERN.sub(r"\1 [REDACTED]", text)
    redacted = _QUERY_SECRET_PATTERN.sub(r"\1=[REDACTED]", redacted)
    redacted = _MAPPING_SECRET_PATTERN.sub(r"\1\2[REDACTED]\4", redacted)
    redacted = _KNOWN_COOKIE_PATTERN.sub(_redact_cookie_match, redacted)
    if redacted.count("=") >= 2 or ";" in redacted:
        redacted = _COOKIE_PATTERN.sub(_redact_cookie_match, redacted)
    return redacted


def redact_log_record(record: dict[str, Any]) -> None:
    record["message"] = redact_text(str(record["message"]))
    if "extra" in record:
        record["extra"] = redact_value(record["extra"])


def _redact_cookie_match(match: re.Match[str]) -> str:
    return f"{match.group(1)}=[REDACTED]"
