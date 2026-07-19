from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from loguru import logger

from utils.qr_login_lite import qrcode_login_lite
from xianyu_connector.infrastructure.account_lock import (
    AccountProcessLock,
    cleanup_stale_profile_singletons,
)
from xianyu_connector.infrastructure.sqlite_secret_repository import SqliteSecretRepository
from xianyu_connector.security.aes_gcm import SecretCipher, load_master_key
from xianyu_connector.security.redaction import redact_log_record


class AccountProcessStopTimeout(TimeoutError):
    pass


def main() -> int:
    if len(sys.argv) != 4:
        return 2
    account_id, user_id_text, session_id = sys.argv[1:]
    logger.configure(patcher=cast(Any, redact_log_record))
    try:
        return _authenticate(account_id, int(user_id_text), session_id)
    except AccountProcessStopTimeout as exc:
        _emit("error", message=str(exc) or "账号连接进程未退出，请重新尝试")
        return 30
    except TimeoutError as exc:
        _emit("expired", message=str(exc) or "二维码已过期")
        return 20
    except Exception as exc:
        logger.exception("browser auth worker failed")
        _emit("error", message=str(exc) or "认证进程失败")
        return 30


def _authenticate(account_id: str, user_id: int, session_id: str) -> int:
    database_path = Path(os.environ["DB_PATH"])
    profile_root = Path(os.environ["XIANYU_PROFILES_ROOT"]) / account_id
    profile_directory = profile_root / "chrome-profile"
    master_key_path = Path(os.environ["XIANYU_MASTER_KEY_PATH"])

    with _account_lock(profile_directory):
        cookies, account = qrcode_login_lite(
            poll_interval=2.0,
            timeout=55.0,
            show_qrcode_in_terminal=False,
            on_qr_url=lambda value: _emit("qr", qr_code_url=_render_qr(value)),
            on_status=lambda value: _emit("status", status=value.lower()),
        )
        scanned_account_id = str(account.get("unb") or "").strip()
        if scanned_account_id != account_id:
            raise ValueError(
                f"扫码账号与目标账号不一致，目标 {account_id}，实际 {scanned_account_id or 'unknown'}"
            )

        cookie_value = "; ".join(f"{key}={value}" for key, value in cookies.items())
        _seed_persistent_profile(profile_directory, cookies)
        _save_identity(profile_root, account, session_id)
        _save_cookie(database_path, master_key_path, account_id, user_id, cookie_value)
        _emit(
            "authenticated",
            account_info={
                "account_id": account_id,
                "is_new_account": False,
                "cookie_length": len(cookie_value),
                "profile_persisted": True,
            },
        )
    return 0


@contextmanager
def _account_lock(profile_directory: Path) -> Iterator[None]:
    deadline = time.monotonic() + 15
    lock: AccountProcessLock | None = None
    while True:
        try:
            candidate = AccountProcessLock(profile_directory)
            candidate.__enter__()
        except BlockingIOError as exc:
            if time.monotonic() >= deadline:
                raise AccountProcessStopTimeout(
                    "账号连接进程未在认证前退出，请重新尝试"
                ) from exc
            time.sleep(0.25)
            continue
        lock = candidate
        break
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def _seed_persistent_profile(profile_directory: Path, cookies: dict[str, str]) -> None:
    from playwright.sync_api import sync_playwright

    profile_directory.mkdir(parents=True, exist_ok=True)
    cleanup_stale_profile_singletons(profile_directory)
    browser_environment = _browser_runtime_environment(profile_directory)
    with sync_playwright() as playwright:
        executable_path = (
            os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
            or playwright.chromium.executable_path
        )
        context = playwright.chromium.launch_persistent_context(
            str(profile_directory),
            headless=True,
            executable_path=executable_path,
            env={**os.environ, **browser_environment},
            args=[
                "--disable-dev-shm-usage",
                "--disable-crash-reporter",
                "--disable-breakpad",
            ],
        )
        try:
            context.add_cookies(
                [
                    {
                        "name": name,
                        "value": value,
                        "domain": ".goofish.com",
                        "path": "/",
                        "secure": True,
                    }
                    for name, value in cookies.items()
                ]
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                "https://www.goofish.com/",
                wait_until="domcontentloaded",
                timeout=10_000,
            )
        finally:
            context.close()


def _browser_runtime_environment(profile_directory: Path) -> dict[str, str]:
    runtime_root = profile_directory.parent / "browser-runtime"
    directories = {
        "HOME": runtime_root / "home",
        "XDG_CONFIG_HOME": runtime_root / "config",
        "XDG_CACHE_HOME": runtime_root / "cache",
        "XDG_RUNTIME_DIR": runtime_root / "runtime",
    }
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
    return {name: str(directory) for name, directory in directories.items()}


def _save_identity(profile_root: Path, account: dict[str, object], session_id: str) -> None:
    profile_root.mkdir(parents=True, exist_ok=True)
    target = profile_root / "device-identity.json"
    temporary = target.with_suffix(".tmp")
    payload = {
        "account_id": str(account.get("unb") or ""),
        "device_id": str(account.get("device_id") or ""),
        "profile_generation": 1,
        "auth_session_id": session_id,
    }
    temporary.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)


def _save_cookie(
    database_path: Path,
    master_key_path: Path,
    account_id: str,
    user_id: int,
    cookie_value: str,
) -> None:
    from db_manager import db_manager

    if not db_manager.update_cookie_account_info(
        account_id,
        cookie_value=cookie_value,
        user_id=user_id,
    ):
        raise RuntimeError("Cookie 写入旧数据库失败")
    repository = SqliteSecretRepository(
        database_path,
        SecretCipher(load_master_key(master_key_path)),
    )
    repository.save(account_id, "cookie", cookie_value)


def _render_qr(value: str) -> str:
    import qrcode

    image = qrcode.make(value)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _emit(event: str, **payload: object) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
