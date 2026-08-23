"""Browser-side primitives for the human verification frame protocol."""

from __future__ import annotations

import base64
import math
import os
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

DEFAULT_VIEWPORT = (1280, 900)
MAX_FRAME_BYTES = 1_500_000
PLATFORM_HOST_SUFFIXES = (".goofish.com", ".taobao.com", ".tmall.com")


class VerificationInputError(ValueError):
    """Raised when an operator input is outside the fixed viewport contract."""


class MouseProtocol(Protocol):
    def move(self, x: float, y: float) -> None: ...

    def down(self, *, button: str) -> None: ...

    def up(self, *, button: str) -> None: ...

    def click(self, x: float, y: float, *, button: str) -> None: ...


class PageProtocol(Protocol):
    mouse: MouseProtocol

    def goto(self, url: str, **options: object) -> object: ...

    def screenshot(self, **options: object) -> bytes: ...


EventSink = Callable[[dict[str, object]], None]


def validate_challenge_url(value: str) -> str:
    if not value or len(value) > 8192 or "\r" in value or "\n" in value:
        raise ValueError("challenge URL is invalid")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host:
        raise ValueError("challenge URL must use HTTPS")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("challenge URL has an invalid port") from exc
    if port not in (None, 443):
        raise ValueError("challenge URL must use the HTTPS port")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("challenge URL must not contain credentials")
    if not any(host == suffix[1:] or host.endswith(suffix) for suffix in PLATFORM_HOST_SUFFIXES):
        raise ValueError("challenge URL host is outside the supported platform")
    return value


class ManualVerificationSession:
    """Apply operator mouse input to one Playwright page and emit safe events."""

    def __init__(
        self,
        page: PageProtocol,
        *,
        account_id: str,
        session_id: str,
        event_sink: EventSink,
        viewport: tuple[int, int] = DEFAULT_VIEWPORT,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        del account_id
        self._page = page
        self._session_id = session_id
        self._event_sink = event_sink
        self._width, self._height = viewport
        self._max_frame_bytes = max_frame_bytes
        self._frame_sequence = 0
        self._opened = False

    @property
    def opened(self) -> bool:
        return self._opened

    @property
    def frame_sequence(self) -> int:
        return self._frame_sequence

    def open(self, challenge_url: str) -> None:
        self._page.goto(
            validate_challenge_url(challenge_url),
            wait_until="domcontentloaded",
            timeout=15_000,
        )
        self._opened = True
        self._event_sink(
            {"event": "state", "state": "waiting_for_operator", "session_id": self._session_id}
        )

    def handle_input(self, payload: Mapping[str, object]) -> None:
        if not self._opened:
            raise VerificationInputError("verification page is not open")
        action = str(payload.get("action") or "")
        x, y = _bounded_coordinates(payload, self._width, self._height)
        if str(payload.get("button") or "left") != "left":
            raise VerificationInputError("only the left mouse button is allowed")
        if action == "move":
            self._page.mouse.move(x, y)
        elif action == "down":
            self._page.mouse.move(x, y)
            self._page.mouse.down(button="left")
        elif action == "up":
            self._page.mouse.move(x, y)
            self._page.mouse.up(button="left")
        elif action == "click":
            self._page.mouse.click(x, y, button="left")
        else:
            raise VerificationInputError("unsupported mouse action")

    def capture_frame(self) -> None:
        image = self._page.screenshot(type="jpeg", quality=70, full_page=False)
        if len(image) > self._max_frame_bytes:
            raise RuntimeError("verification frame exceeds configured size")
        self._frame_sequence += 1
        self._event_sink(
            {
                "event": "frame",
                "session_id": self._session_id,
                "seq": self._frame_sequence,
                "width": self._width,
                "height": self._height,
                "mime_type": "image/jpeg",
                "image_base64": base64.b64encode(image).decode("ascii"),
            }
        )


def browser_runtime_environment(profile_directory: Path) -> dict[str, str]:
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


@contextmanager
def launch_persistent_context(
    profile: Path,
    *,
    headless: bool,
    viewport: tuple[int, int],
) -> Generator[Any, None, None]:
    from playwright.sync_api import sync_playwright

    runtime_environment = browser_runtime_environment(profile)
    with sync_playwright() as playwright:
        executable_path = (
            os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
            or playwright.chromium.executable_path
        )
        context = playwright.chromium.launch_persistent_context(
            str(profile),
            headless=headless,
            executable_path=executable_path,
            viewport={"width": viewport[0], "height": viewport[1]},
            env={**os.environ, **runtime_environment},
            args=[
                "--disable-dev-shm-usage",
                "--disable-crash-reporter",
                "--disable-breakpad",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        try:
            yield context
        finally:
            context.close()


def serialize_platform_cookies(cookies: object) -> str:
    if not isinstance(cookies, list):
        raise ValueError("browser cookies are unavailable")
    values: dict[str, str] = {}
    for item in cookies:
        if not isinstance(item, Mapping):
            continue
        domain = str(item.get("domain") or "").lower().lstrip(".")
        if not _is_platform_domain(domain):
            continue
        name, value = item.get("name"), item.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if not name or any(char in name for char in "=;\r\n"):
            continue
        if any(char in value for char in "\r\n;"):
            continue
        values[name] = value
    if not values:
        raise ValueError("no platform cookies available")
    header = "; ".join(f"{name}={value}" for name, value in values.items())
    if len(header) > 65_536:
        raise ValueError("platform cookies exceed configured size")
    return header


def _bounded_coordinates(payload: Mapping[str, object], width: int, height: int) -> tuple[float, float]:
    raw_x, raw_y = payload.get("x"), payload.get("y")
    if isinstance(raw_x, bool) or not isinstance(raw_x, (int, float, str)):
        raise VerificationInputError("mouse coordinates are required")
    if isinstance(raw_y, bool) or not isinstance(raw_y, (int, float, str)):
        raise VerificationInputError("mouse coordinates are required")
    try:
        x, y = float(raw_x), float(raw_y)
    except (TypeError, ValueError) as exc:
        raise VerificationInputError("mouse coordinates are required") from exc
    if not math.isfinite(x) or not math.isfinite(y) or not 0 <= x < width or not 0 <= y < height:
        raise VerificationInputError("mouse coordinates are outside the viewport")
    return x, y


def _is_platform_domain(domain: str) -> bool:
    return any(
        domain == suffix[1:] or domain.endswith(suffix)
        for suffix in PLATFORM_HOST_SUFFIXES
    )
