from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    ciphertext: bytes
    nonce: bytes
    key_version: int = 1


class SecretCipher:
    def __init__(self, key: bytes, *, key_version: int = 1) -> None:
        if len(key) != 32:
            raise ValueError("AES-256-GCM requires a 32-byte key")
        self._cipher = AESGCM(key)
        self._key_version = key_version

    def encrypt(self, plaintext: str, *, associated_data: bytes) -> EncryptedSecret:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode(), associated_data)
        return EncryptedSecret(ciphertext, nonce, self._key_version)

    def decrypt(self, secret: EncryptedSecret, *, associated_data: bytes) -> str:
        plaintext = self._cipher.decrypt(secret.nonce, secret.ciphertext, associated_data)
        return plaintext.decode()


def load_master_key(path: Path) -> bytes:
    encoded_key = path.read_bytes().strip()
    key = base64.urlsafe_b64decode(encoded_key)
    if len(key) != 32:
        raise ValueError("master key file must contain a base64-encoded 32-byte key")
    return key
