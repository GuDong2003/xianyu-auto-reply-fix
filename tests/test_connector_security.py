import base64
import json
from pathlib import Path

import pytest

from xianyu_connector.security.aes_gcm import SecretCipher, load_master_key
from xianyu_connector.security.redaction import redact_text, redact_value


def test_aes_gcm_round_trip_uses_unique_nonces() -> None:
    cipher = SecretCipher(b"k" * 32)

    first = cipher.encrypt("cookie2=secret", associated_data=b"account-1:cookie")
    second = cipher.encrypt("cookie2=secret", associated_data=b"account-1:cookie")

    assert first.nonce != second.nonce
    assert cipher.decrypt(first, associated_data=b"account-1:cookie") == "cookie2=secret"


def test_aes_gcm_rejects_wrong_account_context() -> None:
    cipher = SecretCipher(b"k" * 32)
    secret = cipher.encrypt("token", associated_data=b"account-1:token")

    with pytest.raises(Exception):
        cipher.decrypt(secret, associated_data=b"account-2:token")


def test_master_key_file_requires_32_bytes(tmp_path: Path) -> None:
    path = tmp_path / "master.key"
    path.write_bytes(base64.urlsafe_b64encode(b"k" * 32))

    assert load_master_key(path) == b"k" * 32


def test_redaction_removes_structured_and_inline_secrets() -> None:
    payload = {
        "authorization": "Bearer abc.def",
        "nested": {"cookie": "cookie2=secret; token=value"},
        "message": "url?x5secdata=secret&sign=signature",
    }

    redacted = redact_value(payload)

    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["nested"]["cookie"] == "[REDACTED]"
    assert "secret" not in redacted["message"]
    assert "signature" not in redacted["message"]


def test_cookie_string_is_redacted_without_destroying_names() -> None:
    text = redact_text("cookie2=secret; _m_h5_tk=token-value")

    assert text == "cookie2=[REDACTED]; _m_h5_tk=[REDACTED]"


def test_single_cookie_and_mapping_repr_are_redacted() -> None:
    text = "Cookie: cookie2=first {'token': 'second', 'cookie2': 'third'} password=fourth"

    redacted = redact_text(text)

    assert "first" not in redacted
    assert "second" not in redacted
    assert "third" not in redacted
    assert "fourth" not in redacted


def test_query_redaction_preserves_json_structure_and_is_idempotent() -> None:
    original = json.dumps(
        {
            "url": "https://example.test/path?token=secret-value",
            "status": "ok",
        }
    )

    redacted = redact_text(original)

    assert json.loads(redacted)["url"].endswith("token=[REDACTED]")
    assert redact_text(redacted) == redacted
