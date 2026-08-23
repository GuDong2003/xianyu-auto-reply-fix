from __future__ import annotations

import base64
import io
import json
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from xianyu_connector import manual_verification_worker as worker_module
from xianyu_connector.infrastructure import verification_process as process_module
from xianyu_connector.infrastructure.verification_browser import (
    ManualVerificationSession,
    VerificationInputError,
    serialize_platform_cookies,
    validate_challenge_url,
)
from xianyu_connector.infrastructure.verification_process import (
    ManagedVerificationProcess,
    VerificationProcessSupervisor,
)


class FakeMouse:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def move(self, x: float, y: float) -> None:
        self.calls.append(("move", (x, y)))

    def down(self, *, button: str) -> None:
        self.calls.append(("down", (button,)))

    def up(self, *, button: str) -> None:
        self.calls.append(("up", (button,)))

    def click(self, x: float, y: float, *, button: str) -> None:
        self.calls.append(("click", (x, y, button)))


class FakePage:
    def __init__(self) -> None:
        self.mouse = FakeMouse()
        self.url = "about:blank"
        self.goto_calls: list[tuple[str, dict[str, object]]] = []

    def goto(self, url: str, **options: object) -> None:
        self.goto_calls.append((url, options))
        self.url = url

    def screenshot(self, **options: object) -> bytes:
        del options
        return b"fake-frame"


def test_fake_challenge_opens_and_emits_frame_without_echoing_url() -> None:
    events: list[dict[str, object]] = []
    page = FakePage()
    session = ManualVerificationSession(
        page,
        account_id="account-1",
        session_id="session-1",
        event_sink=events.append,
        viewport=(640, 480),
    )

    url = "https://challenge.goofish.com/verify?id=redacted"
    session.open(url)
    session.capture_frame()

    assert page.goto_calls[0][0] == url
    assert events[0] == {
        "event": "state",
        "state": "waiting_for_operator",
        "session_id": "session-1",
    }
    frame = events[1]
    assert frame["event"] == "frame"
    assert frame["seq"] == 1
    assert base64.b64decode(str(frame["image_base64"])) == b"fake-frame"
    assert url not in json.dumps(events, ensure_ascii=True)


def test_input_protocol_accepts_bounded_mouse_events() -> None:
    page = FakePage()
    session = ManualVerificationSession(
        page,
        account_id="account-1",
        session_id="session-1",
        event_sink=lambda _: None,
        viewport=(640, 480),
    )

    session.open("https://challenge.goofish.com/verify")
    session.handle_input({"action": "move", "x": 12, "y": 34})
    session.handle_input({"action": "down", "x": 12, "y": 34})
    session.handle_input({"action": "move", "x": 100, "y": 34})
    session.handle_input({"action": "up", "x": 100, "y": 34})

    assert page.mouse.calls == [
        ("move", (12.0, 34.0)),
        ("move", (12.0, 34.0)),
        ("down", ("left",)),
        ("move", (100.0, 34.0)),
        ("move", (100.0, 34.0)),
        ("up", ("left",)),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "move", "x": -1, "y": 10},
        {"action": "move", "x": 640, "y": 10},
        {"action": "move", "x": 1, "y": 480},
        {"action": "move", "x": float("nan"), "y": 1},
        {"action": "keydown", "x": 1, "y": 1},
        {"action": "click", "x": 1, "y": 1, "button": "right"},
    ],
)
def test_input_protocol_rejects_unsafe_events(payload: dict[str, object]) -> None:
    session = ManualVerificationSession(
        FakePage(),
        account_id="account-1",
        session_id="session-1",
        event_sink=lambda _: None,
        viewport=(640, 480),
    )

    with pytest.raises(VerificationInputError):
        session.handle_input(payload)


def test_challenge_url_only_allows_https_platform_hosts() -> None:
    assert validate_challenge_url("https://challenge.goofish.com/verify")
    assert validate_challenge_url("https://login.taobao.com/verify")
    for value in (
        "http://challenge.goofish.com/verify",
        "https://challenge.goofish.com:8443/verify",
        "https://example.com/verify",
        "file:///tmp/challenge.html",
        "javascript:alert(1)",
        "https://challenge.goofish.com/verify\r\nX-Injected: yes",
        "https://user:password@challenge.goofish.com/verify",
        "https://challenge.goofish.com/" + "x" * 8192,
    ):
        with pytest.raises(ValueError):
            validate_challenge_url(value)


def test_supervisor_starts_worker_in_new_process_group_and_does_not_put_url_in_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 1234
        returncode = None

        def __init__(self) -> None:
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(
                '{"event":"state","state":"profile_lock_acquired"}\n'
                '{"event":"state","state":"rfb_ready","rfb_host":"127.0.0.1",'
                '"rfb_port":59123,"session_id":"session-1"}\n'
            )

        def poll(self) -> int | None:
            return self.returncode

    def fake_popen(command: list[str], **options: object) -> FakeProcess:
        calls.append({"command": command, "options": options})
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(process_module, "_reserve_loopback_port", lambda: 59123)
    supervisor = VerificationProcessSupervisor(
        profiles_root=tmp_path,
        python_executable="python-test",
    )

    handle = supervisor.start(
        "account-1",
        "session-1",
        "https://challenge.goofish.com/verify?secret=hidden",
    )

    assert handle.pid == 1234
    assert "secret=hidden" not in " ".join(calls[0]["command"])
    assert calls[0]["options"]["start_new_session"] is True
    assert calls[0]["options"]["stdin"] is subprocess.PIPE
    assert handle.profile_directory == tmp_path / "account-1" / "chrome-profile"


def test_process_reader_keeps_latest_frame_and_terminal_event() -> None:
    class FakeProcess:
        pid = 1234
        returncode = None
        stdin = io.StringIO()
        stdout = io.StringIO(
            '{"event":"frame","seq":1,"image_base64":"a"}\n'
            '{"event":"frame","seq":2,"image_base64":"b"}\n'
            '{"event":"error","code":"invalid_input"}\n'
            '{"event":"state","state":"complete_requested"}\n'
            '{"event":"complete_requested"}\n'
        )

        def poll(self) -> int | None:
            return self.returncode

    handle = ManagedVerificationProcess(
        "account-1",
        "session-1",
        Path("/profiles/account-1/chrome-profile"),
        FakeProcess(),  # type: ignore[arg-type]
    )
    for _ in range(20):
        if handle.wait_for_completion(0.01):
            break
        time.sleep(0.001)

    assert handle.get_frame() == {"event": "frame", "seq": 2, "image_base64": "b"}
    assert handle.get_state() == {"event": "state", "state": "complete_requested"}
    assert handle.wait_for_completion() == {"event": "complete_requested"}


def test_cookie_serialization_filters_non_platform_and_malformed_values() -> None:
    assert serialize_platform_cookies(
        [
            {"domain": ".goofish.com", "name": "session", "value": "new"},
            {"domain": "evil.example", "name": "leak", "value": "no"},
            {"domain": ".goofish.com", "name": "bad", "value": "line\nvalue"},
        ]
    ) == "session=new"


def test_complete_persists_filtered_cookie_without_emitting_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: list[tuple[str, str, str]] = []

    class FakeContext:
        def cookies(self) -> list[dict[str, str]]:
            return [
                {"domain": ".goofish.com", "name": "session", "value": "secret"},
                {"domain": "evil.example", "name": "other", "value": "drop"},
            ]

    class FakeRepository:
        def __init__(self, database_path: Path, cipher: object) -> None:
            del database_path, cipher

        def save(self, account_id: str, secret_type: str, value: str) -> None:
            saved.append((account_id, secret_type, value))

    monkeypatch.setenv("DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("XIANYU_MASTER_KEY_PATH", str(tmp_path / "master.key"))
    monkeypatch.setattr(worker_module, "SqliteSecretRepository", FakeRepository)
    monkeypatch.setattr(worker_module, "SecretCipher", lambda key: key)
    monkeypatch.setattr(worker_module, "load_master_key", lambda path: b"key")

    worker = worker_module.ManualVerificationWorker(
        "account-1", "session-1", tmp_path, event_sink=lambda _: None
    )
    worker._context = FakeContext()
    assert worker._handle_command({"command": "complete"}) == 0
    assert saved == [("account-1", "cookie", "session=secret")]


def test_manual_worker_reports_profile_lock_before_browser_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeContext:
        def __init__(self) -> None:
            self.pages = [FakePage()]

        def new_page(self) -> FakePage:
            return self.pages[0]

    @contextmanager
    def fake_launch(*args: object, **kwargs: object):
        del args, kwargs
        yield FakeContext()

    events: list[dict[str, object]] = []
    monkeypatch.setattr(worker_module, "launch_persistent_context", fake_launch)
    monkeypatch.setattr(worker_module.ManualVerificationWorker, "_start_rfb_server", lambda self: None)

    worker = worker_module.ManualVerificationWorker(
        "account-1",
        "session-1",
        tmp_path,
        event_sink=events.append,
        ttl_seconds=0,
    )

    assert worker.run() == 20
    assert [event["state"] for event in events if event["event"] == "state"][:3] == [
        "starting",
        "profile_lock_acquired",
        "ready",
    ]


def test_stop_reaps_owned_group_after_graceful_worker_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forced: list[float] = []

    class CancelAwareInput(io.StringIO):
        def __init__(self, process: CancelAwareProcess) -> None:
            super().__init__()
            self._process = process

        def flush(self) -> None:
            self._process.returncode = 10

    class CancelAwareProcess:
        pid = 1234
        returncode: int | None = None
        stdout = None

        def __init__(self) -> None:
            self.stdin = CancelAwareInput(self)

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            assert self.returncode is not None
            return self.returncode

    monkeypatch.setattr(
        process_module,
        "_terminate_process_group",
        lambda process, grace, **kwargs: forced.append(grace),
    )
    process = CancelAwareProcess()
    handle = ManagedVerificationProcess(
        "account-1",
        "session-1",
        tmp_path / "chrome-profile",
        process,  # type: ignore[arg-type]
    )

    handle.stop(graceful_seconds=0.1)

    assert '"command":"cancel"' in process.stdin.getvalue()
    assert forced == [10.0]


def test_stop_force_terminates_unresponsive_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forced: list[float] = []

    class UnresponsiveProcess:
        pid = 1234
        returncode = None
        stdin = io.StringIO()
        stdout = None

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("worker", timeout)

    monkeypatch.setattr(
        process_module,
        "_terminate_process_group",
        lambda process, grace, **kwargs: forced.append(grace),
    )
    process = UnresponsiveProcess()
    handle = ManagedVerificationProcess(
        "account-1",
        "session-1",
        tmp_path / "chrome-profile",
        process,  # type: ignore[arg-type]
    )

    handle.stop(graceful_seconds=0.01, grace_seconds=10)

    assert '"command":"cancel"' in process.stdin.getvalue()
    assert forced == [10]
