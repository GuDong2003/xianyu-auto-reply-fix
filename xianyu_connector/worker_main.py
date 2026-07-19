from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, cast

from loguru import logger

from db_manager import db_manager
from xianyu_connector.application.runtime_reporter import RuntimeReporter
from xianyu_connector.domain.connection_failure import (
    ConnectorNetworkFailure,
    ManualVerificationRequired,
)
from xianyu_connector.infrastructure.account_lock import AccountProcessLock
from xianyu_connector.infrastructure.legacy_connection_adapter import LegacyConnectionAdapter
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository
from xianyu_connector.infrastructure.sqlite_secret_repository import SqliteSecretRepository
from xianyu_connector.infrastructure.worker_process import WorkerExitCode
from xianyu_connector.security.aes_gcm import SecretCipher, load_master_key
from xianyu_connector.security.redaction import redact_log_record


def main() -> int:
    logger.configure(patcher=cast(Any, redact_log_record))
    if len(sys.argv) != 2:
        return WorkerExitCode.FATAL
    account_id = sys.argv[1]
    database_path = Path(os.environ["DB_PATH"])
    profiles_root = Path(os.environ["XIANYU_PROFILES_ROOT"])
    master_key_path = Path(os.environ["XIANYU_MASTER_KEY_PATH"])
    profile_directory = profiles_root / account_id / "chrome-profile"

    try:
        with AccountProcessLock(profile_directory):
            return asyncio.run(
                _run_worker(account_id, database_path, profile_directory, master_key_path)
            )
    except BlockingIOError:
        return WorkerExitCode.FATAL


async def _run_worker(
    account_id: str,
    database_path: Path,
    profile_directory: Path,
    master_key_path: Path,
) -> int:
    runtime_repository = SqliteRuntimeRepository(database_path)
    reporter = RuntimeReporter(
        account_id,
        runtime_repository,
        shadow_mode=os.getenv("XIANYU_SHADOW_MODE", "true").lower() == "true",
    )
    secrets = SqliteSecretRepository(
        database_path,
        SecretCipher(load_master_key(master_key_path)),
    )
    cookie = secrets.get(account_id, "cookie") or db_manager.get_cookie(account_id)
    if not cookie:
        reporter.require_manual_verification("cookie_missing", "账号 Cookie 不存在")
        return WorkerExitCode.MANUAL_VERIFICATION
    secrets.save(account_id, "cookie", cookie)

    account_info = db_manager.get_cookie_details(account_id) or {}
    adapter = LegacyConnectionAdapter(
        cookies_str=cookie,
        account_id=account_id,
        user_id=account_info.get("user_id"),
        reporter=reporter,
        token_sink=lambda token, refreshed_at: _save_token(
            secrets,
            account_id,
            token,
            refreshed_at,
        ),
        cookie_sink=lambda value: secrets.save(account_id, "cookie", value),
        verification_sink=lambda value: secrets.save(account_id, "verification_url", value),
        device_id=_load_device_id(profile_directory.parent / "device-identity.json"),
    )
    _restore_token(adapter, secrets.get(account_id, "token"))
    heartbeat_task = asyncio.create_task(_worker_heartbeat(reporter))
    try:
        await adapter.create_session()
        if not await adapter.keep_session_alive():
            if adapter.last_session_keepalive_status == "network_failed":
                return WorkerExitCode.NETWORK_FAILURE
            reporter.require_manual_verification(
                "session_expired",
                adapter.last_session_keepalive_error_message or "轻量 Session 校验失败",
            )
            return WorkerExitCode.SESSION_EXPIRED
        await adapter.main()
        return WorkerExitCode.STOPPED
    except ManualVerificationRequired as exc:
        return _manual_exit_code(exc.reason_code)
    except ConnectorNetworkFailure:
        return WorkerExitCode.NETWORK_FAILURE
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        if adapter.session:
            await adapter.session.close()


async def _worker_heartbeat(reporter: RuntimeReporter) -> None:
    while True:
        reporter.mark_worker_heartbeat(os.getpid())
        await asyncio.sleep(10)


def _save_token(
    repository: SqliteSecretRepository,
    account_id: str,
    token: str,
    refreshed_at: float,
) -> None:
    repository.save(
        account_id,
        "token",
        json.dumps({"token": token, "refreshed_at": refreshed_at}),
    )


def _restore_token(adapter: LegacyConnectionAdapter, payload: str | None) -> None:
    if not payload:
        return
    try:
        parsed = json.loads(payload)
        refreshed_at = float(parsed["refreshed_at"])
        if time.time() - refreshed_at > 2400:
            return
        adapter.current_token = str(parsed["token"])
        adapter.last_token_refresh_time = refreshed_at
        adapter.last_token_refresh_status = "success"
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return


def _load_device_id(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = str(payload.get("device_id") or "").strip()
        return value or None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _manual_exit_code(reason_code: str) -> int:
    if reason_code == "token_expired":
        return WorkerExitCode.TOKEN_EXPIRED
    if reason_code == "session_expired":
        return WorkerExitCode.SESSION_EXPIRED
    if reason_code == "risk_challenge":
        return WorkerExitCode.RISK_CHALLENGE
    return WorkerExitCode.MANUAL_VERIFICATION


def raise_system_exit() -> None:
    raise SystemExit(WorkerExitCode.STOPPED)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: raise_system_exit())
    raise SystemExit(main())
