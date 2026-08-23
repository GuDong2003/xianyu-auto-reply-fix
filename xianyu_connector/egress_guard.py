from __future__ import annotations

import ipaddress
from collections.abc import Callable
from urllib.parse import urlparse
from urllib.request import urlopen


class EgressMismatchError(RuntimeError):
    pass


def verify_fixed_egress(
    expected_ip: str,
    check_url: str,
    *,
    fetch: Callable[[str], str] | None = None,
) -> str:
    expected = str(ipaddress.ip_address(expected_ip.strip()))
    resolver = fetch or _fetch_public_ip
    observed = str(ipaddress.ip_address(resolver(check_url).strip()))
    if observed != expected:
        raise EgressMismatchError(
            f"connector egress mismatch: expected {expected}, observed {observed}"
        )
    return observed


def _fetch_public_ip(url: str) -> str:
    if urlparse(url).scheme != "https":
        raise ValueError("egress check URL must use HTTPS")
    with urlopen(url, timeout=8) as response:  # nosec B310
        payload: bytes = response.read(128)
    return payload.decode("ascii")
