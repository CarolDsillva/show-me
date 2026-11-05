"""Evidence: opaque hashed blobs and one manifest per task. Nothing here is ever unpacked."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re

SHA256 = re.compile(r"^[a-f0-9]{64}$")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def seal(manifest: dict) -> dict:
    """Add the manifest hash. It proves the bundle hasn't changed since now, nothing more."""
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    return {**body, "manifest_sha256": sha256_hex(canonical(body))}


class EvidenceStore:
    def __init__(self, root: pathlib.Path, signer=None) -> None:
        self.root = root
        self.signer = signer

    def _task_dir(self, task_id: str) -> pathlib.Path:
        if not re.fullmatch(r"[a-f0-9]{32}", task_id):
            raise ValueError("Bad task ID")
        path = self.root / task_id
        (path / "blobs").mkdir(parents=True, exist_ok=True)
        return path

    def put_blob(self, task_id: str, data: bytes) -> str:
        digest = sha256_hex(data)
        path = self._task_dir(task_id) / "blobs" / digest
        if not path.exists():
            path.write_bytes(data)
        return digest

    def get_blob(self, task_id: str, digest: str) -> bytes | None:
        if not SHA256.fullmatch(digest):
            return None
        path = self._task_dir(task_id) / "blobs" / digest
        return path.read_bytes() if path.exists() else None

    def write_manifest(self, task_id: str, manifest: dict) -> dict:
        sealed = seal(manifest)
        if self.signer:
            sealed = {**sealed, "signature": self.signer.sign(canonical(sealed))}
        (self._task_dir(task_id) / "manifest.json").write_bytes(canonical(sealed))
        return sealed

    def read_manifest(self, task_id: str) -> dict | None:
        path = self._task_dir(task_id) / "manifest.json"
        return json.loads(path.read_bytes()) if path.exists() else None
