import pytest

from xianyu_connector.egress_guard import EgressMismatchError, verify_fixed_egress


def test_fixed_egress_accepts_expected_ip() -> None:
    observed = verify_fixed_egress(
        "203.0.113.8",
        "https://example.invalid/ip",
        fetch=lambda _: "203.0.113.8\n",
    )

    assert observed == "203.0.113.8"


def test_fixed_egress_fails_closed_on_mismatch() -> None:
    with pytest.raises(EgressMismatchError):
        verify_fixed_egress(
            "203.0.113.8",
            "https://example.invalid/ip",
            fetch=lambda _: "203.0.113.9",
        )
