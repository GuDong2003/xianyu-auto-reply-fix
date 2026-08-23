from __future__ import annotations

import requests

from utils import build_cookies


class _Cookies:
    def get(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None

    def set(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


class _Session:
    def __init__(self) -> None:
        self.proxies: dict[str, str] = {}
        self.headers: dict[str, str] = {}
        self.cookies = _Cookies()
        self.posts: list[str] = []

    def get(self, url: str, **kwargs: object) -> None:
        del kwargs
        if url == "https://log.mmstat.com/eg.js":
            raise requests.ConnectionError("optional analytics DNS unavailable")

    def post(self, url: str, **kwargs: object) -> None:
        del kwargs
        self.posts.append(url)


def test_optional_mmstat_dns_failure_does_not_block_passport_bootstrap(monkeypatch) -> None:
    session = _Session()
    monkeypatch.setattr(build_cookies.requests, "Session", lambda: session)

    result = build_cookies.build_initial_session(with_tfstk=False)

    assert result is session
    assert len(session.posts) == 2
