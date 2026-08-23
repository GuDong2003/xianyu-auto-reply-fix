from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from deploy import remote_verification_pointer_e2e as pointer_e2e

PROJECT_ROOT = Path(__file__).parents[1]


def test_browser_wrapper_environment_uses_ephemeral_state_root(tmp_path: Path) -> None:
    browser_state = tmp_path / "browser-state"

    environment = pointer_e2e._browser_wrapper_environment(
        Path("/ms-playwright/chromium/chrome"),
        browser_state_root=browser_state,
        control_port=9443,
        challenge_port=9444,
    )

    assert environment["XIANYU_POINTER_E2E_BROWSER_STATE"] == str(browser_state)


def test_runner_dispatches_chromium_with_writable_runtime_state(tmp_path: Path) -> None:
    chromium = tmp_path / "chromium"
    chromium.write_text(
        "#!/bin/sh\n"
        "printf 'HOME=%s\\n' \"$HOME\"\n"
        "printf 'XDG_CONFIG_HOME=%s\\n' \"$XDG_CONFIG_HOME\"\n"
        "printf 'XDG_CACHE_HOME=%s\\n' \"$XDG_CACHE_HOME\"\n"
        "printf 'XDG_DATA_HOME=%s\\n' \"$XDG_DATA_HOME\"\n"
        "printf 'XDG_RUNTIME_DIR=%s\\n' \"$XDG_RUNTIME_DIR\"\n"
        "printf 'BREAKPAD_DUMP_LOCATION=%s\\n' \"$BREAKPAD_DUMP_LOCATION\"\n"
        "printf '%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    chromium.chmod(0o700)
    browser_state = tmp_path / "browser-state"

    result = subprocess.run(
        [
            str(PROJECT_ROOT / "deploy" / "run-remote-verification-pointer-e2e.sh"),
            "--user-data-dir=/tmp/profile",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "XIANYU_POINTER_E2E_CHROMIUM": str(chromium),
            "XIANYU_POINTER_E2E_BROWSER_STATE": str(browser_state),
            "XIANYU_POINTER_E2E_HOST_RESOLVER_RULES": (
                "MAP control.test:443 127.0.0.1:9443,"
                "MAP challenge.goofish.com:443 127.0.0.1:9444"
            ),
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"HOME={browser_state / 'home'}",
        f"XDG_CONFIG_HOME={browser_state / 'config'}",
        f"XDG_CACHE_HOME={browser_state / 'cache'}",
        f"XDG_DATA_HOME={browser_state / 'data'}",
        f"XDG_RUNTIME_DIR={browser_state / 'runtime'}",
        f"BREAKPAD_DUMP_LOCATION={browser_state / 'crash'}",
        "--ignore-certificate-errors",
        "--allow-insecure-localhost",
        "--host-resolver-rules=MAP control.test:443 127.0.0.1:9443,MAP challenge.goofish.com:443 127.0.0.1:9444",
        "--user-data-dir=/tmp/profile",
    ]
    for directory in ("home", "config", "cache", "data", "runtime", "crash"):
        path = browser_state / directory
        assert path.is_dir()
        assert path.stat().st_mode & 0o777 == 0o700


def test_runtime_preflight_requires_noexec_tmpfs() -> None:
    pointer_e2e._validate_runtime_tmpfs(
        "tmpfs /tmp tmpfs rw,nosuid,nodev,noexec,size=131072k 0 0\n"
    )

    with pytest.raises(RuntimeError, match="noexec"):
        pointer_e2e._validate_runtime_tmpfs(
            "tmpfs /tmp tmpfs rw,nosuid,nodev,size=131072k 0 0\n"
        )


def test_pointer_drag_requires_multiple_pressed_moves() -> None:
    events = [
        {"type": "pointerdown", "buttons": 1},
        {"type": "pointermove", "buttons": 1},
        {"type": "pointerup", "buttons": 0},
    ]

    with pytest.raises(AssertionError, match="multiple pointermove"):
        pointer_e2e._assert_pointer_drag(events)


def test_pointer_drag_requires_significant_horizontal_delta() -> None:
    events = [
        {"type": "pointerdown", "x": 400, "y": 500, "buttons": 1},
        {"type": "pointermove", "x": 400, "y": 500, "buttons": 1},
        {"type": "pointermove", "x": 401, "y": 500, "buttons": 1},
        {"type": "pointerup", "x": 401, "y": 500, "buttons": 0},
    ]

    with pytest.raises(AssertionError, match="horizontal delta"):
        pointer_e2e._assert_pointer_drag(events)


def test_connection_failure_diagnostic_names_every_transport_layer() -> None:
    diagnostics = pointer_e2e._ConnectionDiagnostics()
    diagnostics.record("service.control", "tcp_ready", port=9443)
    diagnostics.record("http", "response", status=200, url="https://control.test/e2e")
    diagnostics.record("websocket", "opened", url="wss://control.test/api/remote")
    diagnostics.record("rfb", "session_state", browser_state="rfb_ready")

    message = diagnostics.failure_message(
        "remote viewer connection timed out",
        driver_state={"state": "viewer_loading"},
    )
    payload = json.loads(message.partition(": ")[2])

    assert payload["driver_state"] == {"state": "viewer_loading"}
    assert {event["layer"] for event in payload["events"]} == {
        "service.control",
        "http",
        "websocket",
        "rfb",
    }


def test_websocket_handshake_diagnostic_redacts_cookie_values() -> None:
    details = pointer_e2e._websocket_handshake_details(
        [
            (b"host", b"control.test"),
            (b"origin", b"https://control.test"),
            (b"sec-fetch-site", b"same-origin"),
            (b"sec-fetch-mode", b"websocket"),
            (b"sec-fetch-dest", b"empty"),
            (
                b"cookie",
                b"verification_ticket_session=secret; "
                b"verification_remote_proof_session=proof-secret",
            ),
        ]
    )

    assert details == {
        "host": "control.test",
        "origin": "https://control.test",
        "sec_fetch_site": "same-origin",
        "sec_fetch_mode": "websocket",
        "sec_fetch_dest": "empty",
        "cookie_names": [
            "verification_remote_proof_session",
            "verification_ticket_session",
        ],
    }
    assert "secret" not in json.dumps(details)


def test_fetch_metadata_injection_is_diagnostic_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XIANYU_POINTER_E2E_DIAGNOSTIC_FETCH_METADATA", raising=False)
    assert pointer_e2e._diagnostic_fetch_metadata_headers() == {}

    monkeypatch.setenv("XIANYU_POINTER_E2E_DIAGNOSTIC_FETCH_METADATA", "1")
    assert pointer_e2e._diagnostic_fetch_metadata_headers() == {
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "websocket",
        "Sec-Fetch-Dest": "empty",
    }
    effective = pointer_e2e._diagnostic_websocket_headers(
        [(b"origin", b"https://control.test")]
    )
    assert pointer_e2e._websocket_handshake_details(effective) == {
        "host": "",
        "origin": "https://control.test",
        "sec_fetch_site": "same-origin",
        "sec_fetch_mode": "websocket",
        "sec_fetch_dest": "empty",
        "cookie_names": [],
    }


@pytest.mark.skipif(
    os.getenv("XIANYU_RUN_REMOTE_POINTER_E2E") != "1",
    reason="set XIANYU_RUN_REMOTE_POINTER_E2E=1 inside the linux/amd64 production image",
)
def test_remote_pointer_drag_crosses_control_and_connector() -> None:
    subprocess.run(
        [str(PROJECT_ROOT / "deploy" / "run-remote-verification-pointer-e2e.sh")],
        cwd=PROJECT_ROOT,
        check=True,
        timeout=120,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
