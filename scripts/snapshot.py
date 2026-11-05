"""Take a Vultr snapshot of the control plane VM, or list snapshots with --list.

Stdlib only. Reads .secrets/vultr.env and never prints the key. Snapshots are billed by size.
"""

import json
import pathlib
import re
import sys
import urllib.request

API = "https://api.vultr.com/v2"


def env(path: str, name: str) -> str:
    for line in pathlib.Path(path).read_text(encoding="utf-8-sig").splitlines():
        match = re.match(rf"\s*{name}\s*=(.*)$", line)
        if match:
            return match.group(1).strip()
    sys.exit(f"{name} is missing from {path}")


def call(method: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(API + path, json.dumps(body).encode() if body else None, method=method, headers={
        "Authorization": "Bearer " + env(".secrets/vultr.env", "VULTR_API_KEY"), "Content-Type": "application/json",
        "User-Agent": "reprofix-provision",
    })
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def show(snapshot: dict) -> None:
    gib = int(snapshot.get("size") or 0) / 1024**3
    print(snapshot["id"], snapshot["status"], f"{gib:.1f} GiB", snapshot["description"], snapshot["date_created"])


def main() -> None:
    if "--list" in sys.argv:
        for snapshot in call("GET", "/snapshots")["snapshots"]:
            show(snapshot)
        return
    description = sys.argv[1] if len(sys.argv) > 1 else "reprofix"
    show(call("POST", "/snapshots", {"instance_id": env(".secrets/vm.env", "VM_ID"), "description": description})["snapshot"])


if __name__ == "__main__":
    main()
