"""Check a downloaded evidence manifest offline. Needs only `pip install cryptography`.

Usage: python scripts/verify_manifest.py manifest.json [expected public key]

Exits 0 only if the hash and the signature both match. This shows the file hasn't changed since
it was signed. It doesn't show the server that signed it was honest. Pass the expected key to
also check who signed it, because a signature alone only proves it matches its own key.
"""

import base64
import hashlib
import json
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def canonical(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as handle:
        manifest = json.load(handle)
    signature = manifest.pop("signature", None)
    claimed = manifest.pop("manifest_sha256", None)
    hash_ok = hashlib.sha256(canonical(manifest)).hexdigest() == claimed
    print("Hash matches:", hash_ok)
    if not signature:
        print("This manifest isn't signed.")
        return 1
    manifest["manifest_sha256"] = claimed
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(signature["public_key"]))
        key.verify(base64.b64decode(signature["value"]), canonical(manifest))
        signature_ok = True
    except (InvalidSignature, ValueError, KeyError):
        signature_ok = False
    print("Signature matches:", signature_ok)
    key_ok = len(sys.argv) < 3 or sys.argv[2] == signature["public_key"]
    print("Signed by the expected key:", key_ok if len(sys.argv) >= 3 else "not checked")
    return 0 if hash_ok and signature_ok and key_ok else 1


if __name__ == "__main__":
    sys.exit(main())
