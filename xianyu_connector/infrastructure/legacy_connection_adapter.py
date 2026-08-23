from __future__ import annotations

import asyncio
import os
import random
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any, NoReturn
from urllib.parse import urlsplit

from xianyu_connector.application.runtime_reporter import RuntimeReporter
from xianyu_connector.domain.connection_failure import (
    ConnectorNetworkFailure,
    ManualVerificationRequired,
    is_network_failure,
)
from XianyuAutoAsync import ConnectionState, XianyuLive


class LegacyConnectionAdapter(XianyuLive):
    def __init__(
        self,
        *,
        cookies_str: str,
        account_id: str,
        user_id: int | None,
        reporter: RuntimeReporter,
        token_sink: Callable[[str, float], None],
        cookie_sink: Callable[[str], None],
        verification_sink: Callable[[str], None] | None = None,
        device_id: str | None = None,
    ) -> None:
        self._runtime_reporter = reporter
        self._token_sink = token_sink
        self._cookie_sink = cookie_sink
        self._verification_sink = verification_sink
        self._fatal_event = asyncio.Event()
        self._fatal_error: BaseException | None = None
        self._session_failures = 0
        super().__init__(cookies_str, account_id, user_id=user_id)
        if device_id:
            self.device_id = device_id
        self.heartbeat_interval = 15
        self.heartbeat_timeout = 45

    async def main(self) -> None:
        connection_task = asyncio.create_task(super().main())
        fatal_task = asyncio.create_task(self._fatal_event.wait())
        done, pending = await asyncio.wait(
            {connection_task, fatal_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if fatal_task in done and self._fatal_error:
            raise self._fatal_error
        await connection_task

    def _should_defer_auth_recovery_for_qr_grace(
        self,
        current_time: float | None = None,
    ) -> bool:
        self._clear_qr_login_grace_period()
        return False

    def _set_connection_state(self, new_state: ConnectionState, reason: str = "") -> None:
        super()._set_connection_state(new_state, reason)
        if new_state is ConnectionState.CONNECTED:
            self._runtime_reporter.mark_websocket(True, worker_pid=os.getpid())
        elif new_state in {
            ConnectionState.DISCONNECTED,
            ConnectionState.FAILED,
            ConnectionState.CLOSED,
        }:
            self._runtime_reporter.mark_websocket(False, worker_pid=os.getpid())

    async def refresh_token(
        self,
        captcha_retry_count: int = 0,
        allow_password_login_recovery: bool = False,
    ) -> str | None:
        token = await super().refresh_token(
            captcha_retry_count=captcha_retry_count,
            allow_password_login_recovery=False,
        )
        if (
            not token
            and self.last_token_refresh_status == "skipped_cooldown"
            and self.current_token
        ):
            token = self.current_token
        self._runtime_reporter.mark_token(bool(token))
        if token:
            self._token_sink(token, time.time())
            self._cookie_sink(self.cookies_str)
        elif is_network_failure(
            self.last_token_refresh_status,
            self.last_token_refresh_error_message,
        ):
            raise ConnectorNetworkFailure(
                self.last_token_refresh_error_message or "token refresh network failure"
            )
        else:
            await self._notify_and_require_manual(
                "token_expired",
                self.last_token_refresh_error_message or "Token 获取失败，需要人工重新登录",
            )
        return token

    async def keep_session_alive(self) -> bool:
        success = await super().keep_session_alive()
        self._runtime_reporter.mark_session(success)
        if success:
            self._session_failures = 0
            self._cookie_sink(self.cookies_str)
            return True
        if self.last_session_keepalive_status == "auth_failed":
            await self._notify_and_require_manual(
                "session_expired",
                self.last_session_keepalive_error_message,
            )
        self._session_failures += 1
        if self.last_session_keepalive_status == "network_failed" or self._session_failures >= 2:
            raise ConnectorNetworkFailure(
                self.last_session_keepalive_error_message or "session keepalive failed"
            )
        return False

    async def handle_heartbeat_response(self, message_data: Any) -> bool:
        handled = await super().handle_heartbeat_response(message_data)
        if handled:
            self._runtime_reporter.mark_heartbeat()
        return handled

    def _mark_non_heartbeat_message(
        self,
        received_at: float | None = None,
        *,
        is_sync_package: bool = False,
    ) -> None:
        super()._mark_non_heartbeat_message(received_at, is_sync_package=is_sync_package)
        self._runtime_reporter.mark_business_message()

    async def _handle_captcha_verification(
        self,
        res_json: dict[str, Any],
    ) -> NoReturn:
        challenge_url = _challenge_url(res_json)
        if challenge_url and self._verification_sink:
            self._verification_sink(challenge_url)
        message = "平台要求人工完成验证" if challenge_url else "平台要求验证，但未返回可用验证页面，请重新扫码"
        await self._notify_and_require_manual("risk_challenge", message)

    async def _refresh_cookies_via_browser(
        self,
        triggered_by_refresh_token: bool = False,
    ) -> NoReturn:
        await self._notify_and_require_manual("session_expired", "认证已失效，需要重新扫码")

    async def _try_password_login_refresh(
        self,
        trigger_reason: str,
        *args: Any,
        **kwargs: Any,
    ) -> NoReturn:
        await self._notify_and_require_manual("manual_verification_required", trigger_reason)

    async def cookie_refresh_loop(self) -> None:
        await asyncio.Event().wait()

    async def token_refresh_loop(self) -> None:
        next_session_check = 0.0
        next_token_refresh = time.time() + random.uniform(2400, 3000)  # nosec B311
        try:
            while True:
                now = time.time()
                if now >= next_session_check:
                    session_ready = await self.keep_session_alive()
                    interval = random.uniform(600, 840) if session_ready else 60  # nosec B311
                    next_session_check = now + interval
                if now >= next_token_refresh:
                    await self.refresh_token(allow_password_login_recovery=False)
                    next_token_refresh = now + random.uniform(2400, 3000)  # nosec B311
                await self._interruptible_sleep(15)
        except (ManualVerificationRequired, ConnectorNetworkFailure) as exc:
            self._fatal_error = exc
            self._fatal_event.set()
            if self.ws and not getattr(self.ws, "closed", False):
                await self.ws.close()

    async def heartbeat_loop(self, ws: Any) -> None:
        first_unacknowledged_at = self.last_heartbeat_response or time.time()
        while True:
            await self.send_heartbeat(ws)
            await self._interruptible_sleep(self.heartbeat_interval)
            last_ack = self.last_heartbeat_response or first_unacknowledged_at
            if time.time() - last_ack < self.heartbeat_timeout:
                continue
            self._runtime_reporter.mark_websocket(False, worker_pid=os.getpid())
            await ws.close()
            return

    async def send_msg(self, ws: Any, cid: Any, toid: Any, text: Any) -> Any:
        if not self._actions_allowed():
            return None
        return await super().send_msg(ws, cid, toid, text)

    async def send_image_msg(
        self,
        ws: Any,
        cid: Any,
        toid: Any,
        image_url: Any,
        width: int = 800,
        height: int = 600,
        card_id: int | None = None,
    ) -> Any:
        if not self._actions_allowed():
            return None
        return await super().send_image_msg(ws, cid, toid, image_url, width, height, card_id)

    def _is_auto_delivery_trigger(self, send_message: str) -> bool:
        return self._actions_allowed() and super()._is_auto_delivery_trigger(send_message)

    def is_auto_confirm_enabled(self) -> bool:
        return self._actions_allowed() and super().is_auto_confirm_enabled()

    def is_auto_comment_enabled(self) -> bool:
        return self._actions_allowed() and super().is_auto_comment_enabled()

    def _actions_allowed(self) -> bool:
        return self._runtime_reporter.actions_allowed()

    def _require_manual(self, reason_code: str, message: str | None) -> NoReturn:
        safe_message = message or "manual verification required"
        self.last_token_refresh_status = "manual_verification_required"
        self._runtime_reporter.require_manual_verification(reason_code, safe_message)
        raise ManualVerificationRequired(reason_code, safe_message)

    async def _notify_and_require_manual(
        self,
        reason_code: str,
        message: str | None,
    ) -> NoReturn:
        safe_message = message or "manual verification required"
        with suppress(Exception):
            await self.send_token_refresh_notification(
                safe_message,
                "manual_verification_required",
            )
        self._require_manual(reason_code, safe_message)


def _challenge_url(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    value = data.get("url") if isinstance(data, dict) else None
    candidate = str(value or "").strip()
    if len(candidate) > 8192:
        return None
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed = ("goofish.com", "taobao.com", "tmall.com")
    if parsed.scheme != "https" or parsed.port not in (None, 443):
        return None
    if not any(host == domain or host.endswith(f".{domain}") for domain in allowed):
        return None
    return candidate
