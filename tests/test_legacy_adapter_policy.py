from __future__ import annotations

import pytest

from xianyu_connector.domain.connection_failure import is_network_failure
from xianyu_connector.infrastructure.legacy_connection_adapter import LegacyConnectionAdapter


def test_token_failure_classification_distinguishes_network_from_auth() -> None:
    assert is_network_failure("token_refresh_exception", "connection timed out") is True
    assert is_network_failure("token_refresh_failed", "FAIL_SYS_SESSION_EXPIRED") is False


class _RiskSignal(BaseException):
    pass


@pytest.mark.asyncio
async def test_risk_challenge_preserves_valid_platform_url_for_manual_verification() -> None:
    captured: list[str] = []

    class Adapter:
        _verification_sink = captured.append

        async def _notify_and_require_manual(self, reason_code: str, message: str) -> None:
            assert reason_code == "risk_challenge"
            assert message == "平台要求人工完成验证"
            raise _RiskSignal

    challenge_url = "https://passport.goofish.com/verify"
    with pytest.raises(_RiskSignal):
        await LegacyConnectionAdapter._handle_captcha_verification(
            Adapter(),  # type: ignore[arg-type]
            {"data": {"url": challenge_url}},
        )

    assert captured == [challenge_url]


class _FakeWebSocket:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeRuntimeReporter:
    def __init__(self) -> None:
        self.websocket_ready = True

    def mark_websocket(self, ready: bool, *, worker_pid: int | None = None) -> None:
        self.websocket_ready = ready


class _HeartbeatAdapter:
    heartbeat_interval = 15
    heartbeat_timeout = 45
    last_heartbeat_response = 0.0

    def __init__(self) -> None:
        self.clock = 100.0
        self.last_heartbeat_time = 0.0
        self.sent = 0
        self.sleeps = 0
        self._runtime_reporter = _FakeRuntimeReporter()

    async def send_heartbeat(self, ws: _FakeWebSocket) -> None:
        self.sent += 1
        self.last_heartbeat_time = self.clock

    async def _interruptible_sleep(self, seconds: float) -> None:
        self.clock += seconds
        self.sleeps += 1
        if self.sleeps > 4:
            raise AssertionError("heartbeat loop did not close the unacknowledged connection")


@pytest.mark.asyncio
async def test_heartbeat_closes_connection_after_45_seconds_without_ack(monkeypatch) -> None:
    adapter = _HeartbeatAdapter()
    websocket = _FakeWebSocket()
    monkeypatch.setattr(
        "xianyu_connector.infrastructure.legacy_connection_adapter.time.time",
        lambda: adapter.clock,
    )

    await LegacyConnectionAdapter.heartbeat_loop(adapter, websocket)

    assert adapter.sent == 3
    assert websocket.closed is True
    assert adapter._runtime_reporter.websocket_ready is False
