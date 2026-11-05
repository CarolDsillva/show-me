"""The canonical sandbox profile and lifecycle, using the Docker CLI.

This is the Day 1 spike's single source of truth. sandboxd will build on it, so the
values, attestation and destruction rules here are the real ones.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import threading
import time
import uuid
from typing import BinaryIO

RUN_LABEL = "reprofix.run"
DEADLINE_LABEL = "reprofix.deadline"
SANDBOX_USER = "65534:65534"
GIB = 1024**3
MIB = 1024**2

# The only env the sandbox gets, on top of the image's own non-secret ENV.
ENV_ALLOWLIST = {"HOME": "/tmp", "XDG_CACHE_HOME": "/tmp/.cache"}

# The docker client gets a minimal env, so nothing from this process can travel with it.
CLIENT_ENV_KEYS = ("PATH", "HOME", "DOCKER_HOST", "SYSTEMROOT")


@dataclasses.dataclass(frozen=True)
class Profile:
    name: str
    memory_bytes: int
    nano_cpus: int
    pids: int
    nofile: int
    workspace_mib: int
    tmp_mib: int
    shm_mib: int
    command_timeout_s: int
    session_timeout_s: int
    max_stream_bytes: int
    max_artifact_bytes: int


BROWSER_SESSION = Profile(
    name="browser-session",
    memory_bytes=2 * GIB,
    nano_cpus=2_000_000_000,
    pids=512,
    nofile=4096,
    workspace_mib=512,
    tmp_mib=256,
    shm_mib=256,
    command_timeout_s=120,
    session_timeout_s=600,
    max_stream_bytes=1 * MIB,
    max_artifact_bytes=10 * MIB,
)


class AttestationError(RuntimeError):
    """The running container doesn't match the profile."""


class DestructionError(RuntimeError):
    """The container still exists after removal."""


@dataclasses.dataclass
class ExecResult:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    duration_s: float

    @property
    def text(self) -> str:
        return self.stdout.decode(errors="replace") + self.stderr.decode(errors="replace")


def client_env() -> dict[str, str]:
    return {k: os.environ[k] for k in CLIENT_ENV_KEYS if k in os.environ}


def docker_run_args(
    run_id: str, image: str, deadline_epoch: int, profile: Profile = BROWSER_SESSION, runtime: str = "runsc"
) -> list[str]:
    cpus = profile.nano_cpus / 1_000_000_000
    args = [
        "docker", "run", "--detach",
        "--name", container_name(run_id),
        "--label", f"{RUN_LABEL}={run_id}",
        "--label", f"{DEADLINE_LABEL}={deadline_epoch}",
        f"--runtime={runtime}",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--user={SANDBOX_USER}",
        f"--memory={profile.memory_bytes}",
        f"--memory-swap={profile.memory_bytes}",
        f"--cpus={cpus:g}",
        f"--pids-limit={profile.pids}",
        f"--shm-size={profile.shm_mib * MIB}",
        "--ulimit", f"nofile={profile.nofile}:{profile.nofile}",
        "--tmpfs", f"/workspace:rw,nosuid,nodev,size={profile.workspace_mib}m,uid=65534,gid=65534,mode=0700",
        "--tmpfs", f"/tmp:rw,nosuid,nodev,size={profile.tmp_mib}m,uid=65534,gid=65534,mode=0700",
        "--log-driver=none",
    ]
    for key, value in ENV_ALLOWLIST.items():
        args += ["--env", f"{key}={value}"]
    args += [image, "sleep", "infinity"]
    return args


def attest(inspect: dict, profile: Profile = BROWSER_SESSION, runtime: str = "runsc") -> list[str]:
    """Compare what Docker reports with the profile. Returns every mismatch."""
    host = inspect.get("HostConfig", {})
    config = inspect.get("Config", {})
    problems: list[str] = []

    def expect(name: str, actual: object, wanted: object) -> None:
        if actual != wanted:
            problems.append(f"{name}: expected {wanted!r}, found {actual!r}")

    expect("Runtime", host.get("Runtime"), runtime)
    expect("NetworkMode", host.get("NetworkMode"), "none")
    expect("ReadonlyRootfs", host.get("ReadonlyRootfs"), True)
    expect("Privileged", host.get("Privileged"), False)
    expect("CapDrop", host.get("CapDrop"), ["ALL"])
    expect("CapAdd", host.get("CapAdd") or [], [])
    expect("Memory", host.get("Memory"), profile.memory_bytes)
    expect("MemorySwap", host.get("MemorySwap"), profile.memory_bytes)
    expect("NanoCpus", host.get("NanoCpus"), profile.nano_cpus)
    expect("PidsLimit", host.get("PidsLimit"), profile.pids)
    expect("LogType", host.get("LogConfig", {}).get("Type"), "none")
    expect("User", config.get("User"), SANDBOX_USER)
    expect("Binds", host.get("Binds") or [], [])
    expect("Mounts", inspect.get("Mounts") or [], [])
    expect("PidMode", host.get("PidMode") or "", "")
    expect("IpcMode", host.get("IpcMode"), "private")

    if "no-new-privileges" not in (host.get("SecurityOpt") or []):
        problems.append("SecurityOpt: no-new-privileges is missing")
    tmpfs = host.get("Tmpfs") or {}
    expect("Tmpfs paths", sorted(tmpfs), ["/tmp", "/workspace"])
    for path, size in (("/workspace", profile.workspace_mib), ("/tmp", profile.tmp_mib)):
        if f"size={size}m" not in tmpfs.get(path, ""):
            problems.append(f"Tmpfs {path}: size={size}m is missing")

    env_names = {entry.split("=", 1)[0] for entry in config.get("Env") or []}
    for name in env_names:
        upper = name.upper()
        if any(word in upper for word in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")):
            problems.append(f"Env: {name} looks like a credential")
    return problems


def read_capped(stream: BinaryIO, limit: int) -> tuple[bytes, bool]:
    """Keep the first `limit` bytes and discard the rest, so the writer never blocks."""
    kept = bytearray()
    truncated = False
    while chunk := stream.read(65536):
        room = limit - len(kept)
        if room > 0:
            kept += chunk[:room]
        if len(chunk) > room:
            truncated = True
    return bytes(kept), truncated


def container_name(run_id: str) -> str:
    return f"rf-{run_id}"


def _docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, timeout=timeout, env=client_env())


def exists(run_id: str) -> bool:
    return _docker("inspect", container_name(run_id)).returncode == 0


def overdue_containers(now_epoch: float) -> list[str]:
    """Names of our containers past their deadline. A missing or bad label counts as overdue."""
    overdue = []
    for name in labeled_containers():
        result = _docker("inspect", "--format", f'{{{{index .Config.Labels "{DEADLINE_LABEL}"}}}}', name)
        try:
            deadline = int(result.stdout.decode().strip())
        except ValueError:
            deadline = 0
        if deadline < now_epoch:
            overdue.append(name)
    return overdue


def remove_container(name: str) -> bool:
    """Force-remove by name and confirm it is gone."""
    _docker("rm", "--force", "--volumes", name)
    return _docker("inspect", name).returncode != 0


def labeled_containers() -> list[str]:
    result = _docker("ps", "-a", "--filter", f"label={RUN_LABEL}", "--format", "{{.Names}}")
    return result.stdout.decode().split()


class Sandbox:
    """One container, attested on entry and destroyed on exit, on every path."""

    def __init__(self, image: str, profile: Profile = BROWSER_SESSION, runtime: str = "runsc") -> None:
        self.image = image
        self.profile = profile
        self.runtime = runtime
        self.run_id = uuid.uuid4().hex[:16]
        self.name = container_name(self.run_id)
        self.attestation: dict | None = None
        self.destroyed = False

    def __enter__(self) -> "Sandbox":
        deadline = int(time.time()) + self.profile.session_timeout_s
        args = docker_run_args(self.run_id, self.image, deadline, self.profile, self.runtime)
        started = subprocess.run(args, capture_output=True, timeout=120, env=client_env())
        if started.returncode != 0:
            self._force_remove()
            raise RuntimeError(f"Sandbox failed to start: {started.stderr.decode(errors='replace')[:800]}")
        try:
            self.attestation = self.inspect()
            problems = attest(self.attestation, self.profile, self.runtime)
            if problems:
                raise AttestationError("; ".join(problems))
        except BaseException:
            self.destroy()
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        self.destroy()

    def inspect(self) -> dict:
        result = _docker("inspect", self.name)
        if result.returncode != 0:
            raise RuntimeError(f"Inspect failed: {result.stderr.decode(errors='replace')[:400]}")
        return json.loads(result.stdout)[0]

    def state(self) -> dict:
        return self.inspect()["State"]

    def exec(
        self, command: list[str], stdin: bytes | None = None, timeout: int | None = None, max_bytes: int | None = None
    ) -> ExecResult:
        """Run a command with limits enforced from the host.

        On timeout the whole container is killed with docker kill, because nothing
        inside it can be trusted to stop.
        """
        limit = max_bytes or self.profile.max_stream_bytes
        wall = timeout or self.profile.command_timeout_s
        started = time.monotonic()
        process = subprocess.Popen(
            ["docker", "exec", "-i", self.name, *command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=client_env(),
        )
        captured: dict[str, tuple[bytes, bool]] = {}

        def reader(name: str, stream: BinaryIO) -> None:
            captured[name] = read_capped(stream, limit)

        threads = [
            threading.Thread(target=reader, args=("out", process.stdout), daemon=True),
            threading.Thread(target=reader, args=("err", process.stderr), daemon=True),
        ]
        for thread in threads:
            thread.start()

        def feed() -> None:
            try:
                if stdin:
                    process.stdin.write(stdin)
                process.stdin.close()
            except OSError:
                pass  # The command may legitimately exit before reading its input.

        threading.Thread(target=feed, daemon=True).start()

        timed_out = False
        try:
            process.wait(timeout=wall)
        except subprocess.TimeoutExpired:
            timed_out = True
            _docker("kill", self.name)
            process.kill()
            process.wait()
        for thread in threads:
            thread.join(timeout=10)

        out, out_truncated = captured.get("out", (b"", False))
        err, err_truncated = captured.get("err", (b"", False))
        return ExecResult(
            exit_code=None if timed_out else process.returncode,
            stdout=out, stderr=err,
            stdout_truncated=out_truncated, stderr_truncated=err_truncated,
            timed_out=timed_out, duration_s=time.monotonic() - started,
        )

    def is_alive(self) -> bool:
        """True only if a command really runs. Docker keeps reporting "running" after
        gVisor itself has died, so `docker inspect` can't answer this."""
        result = self.exec(["true"], timeout=20)
        return result.exit_code == 0

    def open_stream(self, command: list[str]) -> subprocess.Popen:
        """A long-lived command with stdin and stdout pipes. The caller caps every read."""
        return subprocess.Popen(
            ["docker", "exec", "-i", self.name, *command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=client_env(),
        )

    def _force_remove(self) -> None:
        _docker("rm", "--force", "--volumes", self.name)

    def destroy(self) -> None:
        self._force_remove()
        if exists(self.run_id):
            raise DestructionError(f"{self.name} still exists after removal")
        self.destroyed = True
