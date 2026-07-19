from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from xianyu_connector.domain.account_state import AccountState
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository


def build_legacy_runtime_status(database_path: Path, account_id: str) -> dict[str, Any]:
    runtime = SqliteRuntimeRepository(database_path).get(account_id)
    if not runtime:
        return _empty_status()

    readiness = runtime.readiness
    running_states = {
        AccountState.AUTHENTICATING,
        AccountState.CONNECTING,
        AccountState.ONLINE,
        AccountState.DEGRADED,
        AccountState.RECOVERING,
    }
    connection_state = {
        AccountState.ONLINE: "connected",
        AccountState.CONNECTING: "connecting",
        AccountState.DEGRADED: "reconnecting",
        AccountState.RECOVERING: "reconnecting",
    }.get(runtime.state, runtime.state.value)
    heartbeat_at = _timestamp(runtime.last_heartbeat_ack_at)
    session_at = _timestamp(runtime.last_session_keepalive_at)
    business_at = _timestamp(runtime.last_business_message_at)
    return {
        **_empty_status(),
        "instance_exists": runtime.worker_pid is not None,
        "running": runtime.state in running_states,
        "connection_state": connection_state,
        "connector_state": runtime.state.value,
        "connector_reason_code": runtime.reason_code,
        "connector_reason_message": runtime.reason_message,
        "ws_ready": readiness.websocket_ready,
        "session_ready": readiness.session_ready,
        "has_current_token": readiness.token_ready,
        "message_stream_ready": readiness.stream_ready,
        "message_stream_status": "healthy" if readiness.stream_ready else "not_ready",
        "session_keepalive_status": "success" if readiness.session_ready else "not_ready",
        "token_refresh_status": "success" if readiness.token_ready else "not_ready",
        "last_heartbeat_response_at": heartbeat_at,
        "session_keepalive_at": session_at,
        "last_business_activity_at": business_at,
        "last_message_received_at": business_at,
        "state_last_changed_at": _timestamp(runtime.entered_at),
        "worker_pid": runtime.worker_pid,
        "profile_generation": runtime.profile_generation,
        "restart_count": runtime.restart_count,
        "readiness": {
            "session": readiness.session_ready,
            "token": readiness.token_ready,
            "websocket": readiness.websocket_ready,
            "stream": readiness.stream_ready,
            "online": readiness.online,
        },
    }


def _empty_status() -> dict[str, Any]:
    return {
        "instance_exists": False,
        "running": False,
        "connection_state": "not_running",
        "ws_ready": False,
        "session_ready": False,
        "has_current_token": False,
        "message_stream_ready": False,
        "message_stream_status": "not_running",
        "token_refresh_status": None,
        "session_keepalive_status": None,
        "last_heartbeat_response_at": None,
        "session_keepalive_at": None,
        "last_business_activity_at": None,
        "last_message_received_at": None,
        "state_last_changed_at": None,
    }


def _timestamp(value: datetime | None) -> float | None:
    return value.timestamp() if value else None
