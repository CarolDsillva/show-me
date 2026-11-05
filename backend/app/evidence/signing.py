"""Ed25519 signatures for evidence manifests.

A signature shows a manifest hasn't changed since this server signed it. It doesn't show the
server was honest. The private key stays with the API process, and never reaches a sandbox.
"""

from __future__ import annotations

import base64
import os
import pathlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ALGORITHM = "Ed25519"


class Signer:
    def __init__(self, key_path: pathlib.Path) -> None:
        self.key = self._load_or_create(key_path)
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public_key = base64.b64encode(raw).decode()

    @staticmethod
    def _load_or_create(path: pathlib.Path) -> Ed25519PrivateKey:
        if path.exists():
            return Ed25519PrivateKey.from_private_bytes(path.read_bytes())
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        # O_EXCL: if another process wrote the key first, fail rather than replace it.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
        return key

    def sign(self, message: bytes) -> dict:
        return {
            "algorithm": ALGORITHM,
            "public_key": self.public_key,
            "value": base64.b64encode(self.key.sign(message)).decode(),
        }


def verify(message: bytes, signature: dict) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(signature["public_key"]))
        key.verify(base64.b64decode(signature["value"]), message)
        return True
    except (InvalidSignature, ValueError, KeyError, TypeError):
        return False
