from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from enum import StrEnum


class ProofStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ProofVerification:
    status: ProofStatus
    expires_at: int | None = None
    user_id: str | None = None


class OperatorProofSigner:
    def __init__(self, secret: str, *, ttl_seconds: int = 300) -> None:
        if len(secret) < 32:
            raise ValueError("remote verification proof secret must be at least 32 characters")
        if ttl_seconds <= 0:
            raise ValueError("remote verification proof TTL must be positive")
        self._secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds

    def issue(
        self,
        user_id: int | str,
        account_id: str,
        session_id: str,
        *,
        now: int | float | None = None,
    ) -> str:
        issued_at = int(time.time() if now is None else now)
        payload = {
            "account_id": account_id,
            "expires_at": issued_at + self.ttl_seconds,
            "session_id": session_id,
            "user_id": str(user_id),
            "version": 1,
        }
        encoded = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        signature = _encode(hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def verify(
        self,
        token: str,
        user_id: int | str,
        account_id: str,
        session_id: str,
        *,
        now: int | float | None = None,
    ) -> ProofVerification:
        result = self.verify_bound(token, account_id, session_id, now=now)
        if result.user_id != str(user_id):
            return ProofVerification(ProofStatus.INVALID)
        return result

    def verify_bound(
        self,
        token: str,
        account_id: str,
        session_id: str,
        *,
        now: int | float | None = None,
    ) -> ProofVerification:
        payload = self._decode_verified(token)
        if payload is None:
            return ProofVerification(ProofStatus.INVALID)
        user_id = payload.get("user_id")
        if (
            payload.get("version") != 1
            or not isinstance(user_id, str)
            or not user_id
            or payload.get("account_id") != account_id
            or payload.get("session_id") != session_id
        ):
            return ProofVerification(ProofStatus.INVALID)
        expires_at = payload.get("expires_at")
        if not isinstance(expires_at, int):
            return ProofVerification(ProofStatus.INVALID)
        current_time = int(time.time() if now is None else now)
        status = ProofStatus.EXPIRED if current_time >= expires_at else ProofStatus.VALID
        return ProofVerification(status, expires_at, user_id)

    def _decode_verified(self, token: str) -> dict[str, object] | None:
        try:
            encoded, supplied_signature = token.split(".", 1)
            expected_signature = _encode(
                hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(supplied_signature, expected_signature):
                return None
            payload = json.loads(_decode(encoded))
        except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8")
