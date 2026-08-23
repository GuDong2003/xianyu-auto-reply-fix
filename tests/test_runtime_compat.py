from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException, Response

from reply_server import _require_legacy_connection_mode, verify
from xianyu_connector.application.runtime_reporter import RuntimeReporter
from xianyu_connector.application.runtime_service import RuntimeService
from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository
from xianyu_control.runtime_compat import build_legacy_runtime_status
from XianyuAutoAsync import log_captcha_event


def test_runtime_compat_exposes_four_connector_signals(tmp_path: Path) -> None:
    database_path = tmp_path / "compat.db"
    with sqlite3.connect(database_path) as connection:
        apply_connector_schema(connection)
    repository = SqliteRuntimeRepository(database_path)
    RuntimeService(repository).transition_to("account-1", AccountState.AUTHENTICATING)
    reporter = RuntimeReporter("account-1", repository)
    reporter.mark_session(True)
    reporter.mark_token(True)
    reporter.mark_websocket(True)
    reporter.mark_heartbeat()

    status = build_legacy_runtime_status(database_path, "account-1")

    assert status["connector_state"] == "online"
    assert status["connection_state"] == "connected"
    assert status["readiness"]["online"] is True


def test_runtime_compat_returns_offline_shape_for_unknown_account(tmp_path: Path) -> None:
    database_path = tmp_path / "compat.db"
    with sqlite3.connect(database_path) as connection:
        apply_connector_schema(connection)

    status = build_legacy_runtime_status(database_path, "missing")

    assert status["running"] is False
    assert status["connection_state"] == "not_running"


def test_runtime_compat_prioritizes_manual_connector_state(tmp_path: Path) -> None:
    database_path = tmp_path / "compat.db"
    with sqlite3.connect(database_path) as connection:
        apply_connector_schema(connection)
    repository = SqliteRuntimeRepository(database_path)
    reporter = RuntimeReporter("account-1", repository)

    reporter.require_manual_verification("risk_challenge", "平台要求人工完成验证")
    status = build_legacy_runtime_status(database_path, "account-1")

    assert status["connector_state"] == "manual_verification_required"
    assert status["connector_reason_code"] == "risk_challenge"
    assert status["running"] is False
    assert status["readiness"]["online"] is False


def test_external_connector_mode_blocks_legacy_authentication(monkeypatch) -> None:
    monkeypatch.setenv("XIANYU_EXTERNAL_CONNECTOR", "true")

    with pytest.raises(HTTPException) as error:
        _require_legacy_connection_mode()

    assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("external_connector", "expected_mode"),
    [("true", "external_connector"), ("false", "legacy")],
)
async def test_verify_exposes_connection_mode(
    monkeypatch: pytest.MonkeyPatch,
    external_connector: str,
    expected_mode: str,
) -> None:
    monkeypatch.setenv("XIANYU_EXTERNAL_CONNECTOR", external_connector)

    authenticated_response = Response()
    authenticated = await verify(
        authenticated_response,
        {"user_id": 7, "username": "seller", "is_admin": False}
    )
    anonymous_response = Response()
    anonymous = await verify(anonymous_response, None)

    assert authenticated["connection_mode"] == expected_mode
    assert anonymous == {"authenticated": False, "connection_mode": expected_mode}
    assert authenticated_response.headers["cache-control"] == "no-store, private, max-age=0"
    assert anonymous_response.headers["cache-control"] == "no-store, private, max-age=0"


def test_external_connector_mode_does_not_write_legacy_captcha_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XIANYU_EXTERNAL_CONNECTOR", "true")

    log_captcha_event("account-1", "risk challenge", details="sensitive URL")

    assert not (tmp_path / "logs" / "captcha_verification.txt").exists()
