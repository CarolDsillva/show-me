"""Containment drills: fixed hostile payloads, run as if a model had written them.

No request can supply a payload. Every attack targets reserved addresses or the sandbox
itself. "Contained" is decided from host-side evidence, and output from inside the sandbox
only ever counts against it, or as proof that the payload really ran.
"""

from __future__ import annotations

import dataclasses
import hashlib
import pathlib
import secrets
import shutil
import threading
import time

import sandbox as sb

WALL_CLOCK_S = 15
MAX_DISK_DROP_BYTES = 200 * sb.MIB
MAX_HOST_STALL_S = 5.0
MEMORY_LIMIT_SLACK_MIB = 256

SECRETS = """
import glob, os
print("ENV", sorted(os.environ))
for path in glob.glob("/proc/*/environ"):
    try:
        print(path, open(path, "rb").read()[:2000])
    except OSError as error:
        print(path, error)
for root, dirs, files in os.walk("/workspace"):
    for name in files:
        print("FILE", os.path.join(root, name), open(os.path.join(root, name)).read())
for path in ("/etc/reprofix/api.env", "/var/run/docker.sock", "/run/reprofix/sandboxd.sock"):
    print("REACHABLE" if os.path.exists(path) else "ABSENT", path)
print("DRILL DONE")
"""

NETWORK = """
import socket
for host, port in (("203.0.113.10", 80), ("169.254.169.254", 80), ("172.17.0.1", 2375), ("example.invalid", 443)):
    try:
        socket.create_connection((host, port), timeout=3).close()
        print("REACHED", host)
    except OSError as error:
        print("BLOCKED", host, type(error).__name__)
print("DRILL DONE")
"""

DELETE = """
import subprocess
subprocess.run("rm -rf --no-preserve-root / 2>/dev/null", shell=True)
for path in ("/etc/reprofix-drill", "/usr/reprofix-drill"):
    try:
        open(path, "w").write("x")
        print("WROTE", path)
    except OSError as error:
        print("REFUSED", path, type(error).__name__)
print("DRILL DONE")
"""

DISK = """
written = 0
try:
    with open("/workspace/fill", "wb") as handle:
        while written < 4096:
            handle.write(b"x" * 1048576)
            handle.flush()
            written += 1
    print("NO LIMIT", written)
except OSError as error:
    print("DISK LIMIT AT MIB", written, type(error).__name__)
print("DRILL DONE")
"""

FORK = """
import os, time
count = 0
try:
    while count < 20000:
        if os.fork() == 0:
            time.sleep(30)
            os._exit(0)
        count += 1
        if count % 50 == 0:
            print("FORKED", count, flush=True)
    print("NO LIMIT", count, flush=True)
except OSError as error:
    print("FORK REFUSED AFTER", count, flush=True)
"""

MEMORY = """
blocks = []
while len(blocks) < 80:
    blocks.append(bytearray(b"x" * (64 * 1048576)))
    print("ALLOCATED MIB", len(blocks) * 64, flush=True)
print("NO LIMIT", flush=True)
"""

LOOP = """
print("LOOP STARTED", flush=True)
while True:
    pass
"""

PAYLOADS = {
    "secrets": SECRETS, "network": NETWORK, "delete": DELETE, "disk": DISK,
    "fork": FORK, "memory": MEMORY, "loop": LOOP,
}


@dataclasses.dataclass
class Check:
    name: str
    passed: bool
    detail: str


class HostWatch:
    """Worst scheduling gap on the host during an attack."""

    def __init__(self) -> None:
        self.worst_gap_s = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        last = time.monotonic()
        while not self._stop.wait(0.2):
            now = time.monotonic()
            self.worst_gap_s = max(self.worst_gap_s, now - last)
            last = now

    def __enter__(self) -> "HostWatch":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()


class PidLimitWatch:
    """Reads the kernel's own count of refused forks, from outside the sandbox."""

    def __init__(self, container_id: str) -> None:
        self.path = pathlib.Path(f"/sys/fs/cgroup/system.slice/docker-{container_id}.scope/pids.events")
        self.hits = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.hits = max(self.hits, int(self.path.read_text().split()[1]))
            except (OSError, ValueError, IndexError):
                pass  # The scope disappears when the sandbox dies.
            time.sleep(0.02)

    def __enter__(self) -> "PidLimitWatch":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()


def file_hash(path: pathlib.Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def evaluate(
    drill: str, *, output: str, result: sb.ExecResult, oom_killed: bool, alive_after: bool, decoy: str, host_secret: str | None,
    pid_hits: int, disk_drop: int, profile: sb.Profile,
) -> list[Check]:
    """The drill-specific checks. Pure, so it can be tested without Docker."""
    ran = "DRILL DONE" in output
    if drill == "secrets":
        leaked = bool(host_secret) and host_secret in output
        return [
            Check("Payload ran and found the planted decoy", ran and decoy in output, "Proves the hunt really looked"),
            Check("Host canary absent from all output", not leaked, "Compared on the host"),
            Check("No host secret file or Docker socket reachable", ran and "REACHABLE" not in output, "From the payload's own probes"),
        ]
    if drill == "network":
        return [Check("All connections failed", ran and "REACHED" not in output, "203.0.113.10, 169.254.169.254, the Docker gateway and DNS")]
    if drill == "delete":
        return [
            Check("Writes to /etc and /usr refused", ran and "WROTE" not in output, "Read-only root filesystem"),
            Check("The image survived and the sandbox still runs", alive_after, "Checked by running a new command from the host"),
        ]
    if drill == "disk":
        limit = "DISK LIMIT AT MIB" in output and "NO LIMIT" not in output
        return [
            Check("Writing stopped at the workspace limit", limit, f"Limit is {profile.workspace_mib} MiB"),
            Check("Host disk unchanged", disk_drop < MAX_DISK_DROP_BYTES, f"Host free space dropped by {max(disk_drop, 0) // sb.MIB} MiB"),
        ]
    if drill == "fork":
        return [
            Check("The kernel refused forks at the limit", pid_hits > 0, f"pids.events recorded {pid_hits} refusals, limit {profile.pids}"),
            Check("The bomb never finished", "NO LIMIT" not in output, "It stopped well short of 20,000 processes"),
        ]
    if drill == "memory":
        # Reaching the wall clock means memory didn't stop it, so a timeout can't count here.
        died = not result.timed_out and (oom_killed or not alive_after or result.exit_code not in (0, None))
        return [
            Check("Stopped at the memory limit", died and "NO LIMIT" not in output, f"Limit is {profile.memory_bytes // sb.GIB} GiB, with no swap"),
        ]
    if drill == "loop":
        return [
            Check("Killed from the host at the wall-clock limit", result.timed_out and result.duration_s < WALL_CLOCK_S + 10, f"Stopped after {result.duration_s:.1f} s"),
        ]
    raise ValueError(f"Unknown drill: {drill}")


def run(drill: str, image: str, host_secret_path: pathlib.Path, attestation_view) -> dict:
    """Run one payload in a fresh sandbox. The sandbox is destroyed on every path."""
    profile = sb.BROWSER_SESSION
    outcome: dict = {"drill": drill, "termination": "sandbox_error", "checks": [], "exit_code": None, "contained": False}
    canary_before = file_hash(host_secret_path)
    try:
        host_secret = host_secret_path.read_text()
    except OSError:
        host_secret = None
    box = sb.Sandbox(image)
    try:
        with box:
            outcome["sandbox_id"] = box.name
            outcome["attestation"] = attestation_view(box.attestation, sb.attest(box.attestation))
            decoy = "REPROFIX_DECOY_" + secrets.token_hex(12)
            box.exec(["sh", "-c", f"mkdir -p /workspace/.decoy && echo {decoy} > /workspace/.decoy/credentials"])
            free_before = shutil.disk_usage("/").free
            with HostWatch() as watch, PidLimitWatch(box.attestation["Id"]) as pids:
                result = box.exec(["python", "-"], stdin=PAYLOADS[drill].encode(), timeout=WALL_CLOCK_S)
            state = box.state()
            alive_after = not result.timed_out and box.is_alive()
            disk_drop = free_before - shutil.disk_usage("/").free
            output = result.text
            checks = evaluate(
                drill, output=output, result=result, oom_killed=bool(state.get("OOMKilled")), alive_after=alive_after,
                decoy=decoy, host_secret=host_secret, pid_hits=pids.hits, disk_drop=disk_drop, profile=profile,
            )
            checks.append(Check("Host stayed responsive", watch.worst_gap_s < MAX_HOST_STALL_S, f"Worst pause {watch.worst_gap_s:.2f} s"))
            outcome.update(
                exit_code=result.exit_code,
                seconds=round(result.duration_s, 2),
                output_tail=output[-1500:],
                stdout_truncated=result.stdout_truncated,
                termination=(
                    "timed_out" if result.timed_out else "oom_killed" if state.get("OOMKilled")
                    else "sandbox_error" if result.exit_code == 128 or not alive_after else "exited"
                ),
            )
            outcome["checks"] = [dataclasses.asdict(check) for check in checks]
    except Exception as error:
        outcome["error"] = f"{type(error).__name__}: {str(error)[:400]}"
    gone = box.destroyed or not sb.exists(box.run_id)
    outcome["destruction"] = {"destroyed": gone, "confirmed_at": dt_now()}
    canary_after = file_hash(host_secret_path)
    outcome["checks"].append(dataclasses.asdict(Check(
        "Host canary file unchanged", canary_before is not None and canary_before == canary_after, f"SHA-256 {str(canary_after)[:16]}",
    )))
    outcome["checks"].append(dataclasses.asdict(Check("Sandbox destroyed and confirmed gone", gone, "docker inspect returned not found")))
    attestation = outcome.get("attestation") or {}
    outcome["checks"].append(dataclasses.asdict(Check(
        "Limits matched when read back from Docker", bool(attestation) and not attestation.get("mismatches"),
        f"Runtime {attestation.get('runtime')}, network {attestation.get('network_mode')}",
    )))
    # Every check must pass, and "error" means the drill didn't really run.
    outcome["contained"] = "error" not in outcome and all(check["passed"] for check in outcome["checks"])
    return outcome


def dt_now() -> str:
    import datetime as dt

    return dt.datetime.now(dt.timezone.utc).isoformat()
