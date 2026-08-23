from __future__ import annotations

import os
from pathlib import Path


class ProductionBrowserUnavailable(RuntimeError):
    pass


def resolve_production_chromium_executable() -> Path:
    configured_path = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "").strip()
    executable = Path(configured_path) if configured_path else _playwright_executable()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ProductionBrowserUnavailable(
            f"production Chromium is unavailable: {executable}"
        )
    return executable


def _playwright_executable() -> Path:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        return Path(playwright.chromium.executable_path)
