"""SEC-21: look for our real secrets in all of git history and in the sandbox image.

Run before anything goes public. Prints names and counts only, never a value. Exits non-zero
if it finds anything. The image scan runs on the VM, so it gets only the secrets the VM already
holds, over SSH stdin. The Vultr account key stays on this machine, so it is checked in git only.
"""

import base64
import json
import pathlib
import re
import subprocess
import sys

VM_HELD = {"VULTR_INFERENCE_API_KEY", "GITHUB_TOKEN", "OPERATOR_PASSCODE"}
REMOTE = r"""
import base64, json, os, subprocess, sys, tarfile, tempfile
values = {k: base64.b64decode(v) for k, v in json.load(sys.stdin).items()}
image = [l.split("=", 1)[1].strip() for l in open("/etc/reprofix/sandboxd.env") if l.startswith("REPROFIX_IMAGE=")][0]
hits = {k: 0 for k in values}
files = 0
with tempfile.TemporaryDirectory(dir="/var/tmp") as temp:
    out = os.path.join(temp, "image.tar")
    subprocess.run(["docker", "save", "-o", out, image], check=True)
    def scan(handle):
        global files
        with tarfile.open(fileobj=handle, mode="r:*") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                data = archive.extractfile(member).read()
                files += 1
                if member.name.startswith("blobs/") or member.name.endswith((".tar", "layer.tar")):
                    import io
                    try:
                        scan(io.BytesIO(data))
                        continue
                    except tarfile.TarError:
                        pass
                for key, value in values.items():
                    if value in data:
                        hits[key] += 1
    with open(out, "rb") as handle:
        scan(handle)
config = subprocess.run(["docker", "inspect", image], capture_output=True).stdout
history = subprocess.run(["docker", "history", "--no-trunc", image], capture_output=True).stdout
for key, value in values.items():
    if value in config or value in history:
        hits[key] += 1
print(json.dumps({"image": image[:24], "files_scanned": files, "hits": hits}))
"""


def secrets() -> dict[str, bytes]:
    found = {}
    for path in (".secrets/vultr.env", ".secrets/api.env"):
        for line in pathlib.Path(path).read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"\s*([A-Z_]+)\s*=(.*)$", line)
            if match and match.group(2).strip():
                found[match.group(1)] = match.group(2).strip().encode()
    return found


def scan_git(values: dict[str, bytes]) -> dict[str, int]:
    """Every blob reachable in any commit, plus commit messages."""
    hits = {name: 0 for name in values}
    objects = subprocess.run(["git", "rev-list", "--all", "--objects"], capture_output=True, text=True, check=True).stdout
    shas = [line.split()[0] for line in objects.splitlines()]
    print("git objects scanned:", len(shas))
    for sha in shas:
        data = subprocess.run(["git", "cat-file", "-p", sha], capture_output=True).stdout
        for name, value in values.items():
            if value in data:
                hits[name] += 1
    return hits


def main() -> None:
    values = secrets()
    print("secrets checked:", sorted(values))
    git_hits = scan_git(values)
    print("git history hits:", git_hits)
    vm = dict(line.split("=", 1) for line in pathlib.Path(".secrets/vm.env").read_text().split())
    on_vm = {k: v for k, v in values.items() if k in VM_HELD}
    payload = json.dumps({k: base64.b64encode(v).decode() for k, v in on_vm.items()})
    command = ["ssh", "-i", str(pathlib.Path.home() / ".ssh/reprofix_ed25519"), "-o", "BatchMode=yes", f"root@{vm['VM_IP']}"]
    upload = subprocess.run([*command, "cat > /var/tmp/rf-scan.py"], input=REMOTE.encode(), capture_output=True)
    if upload.returncode != 0:
        sys.exit("Couldn't copy the scanner to the VM")
    result = subprocess.run([*command, "python3 /var/tmp/rf-scan.py; rm -f /var/tmp/rf-scan.py"], input=payload.encode(), capture_output=True, timeout=500)
    print("image scan:", result.stdout.decode().strip() or result.stderr.decode()[-400:])
    report = json.loads(result.stdout) if result.returncode == 0 else None
    clean = report is not None and not any(git_hits.values()) and not any(report["hits"].values())
    print("CLEAN" if clean else "NOT CLEAN")
    sys.exit(0 if clean else 1)


if __name__ == "__main__":
    main()
