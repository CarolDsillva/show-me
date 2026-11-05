"""SEC-01 to SEC-08, plus SEC-10, SEC-13 and SEC-17. See the sandbox-security skill.

Payloads use reserved addresses and fake values only. Assertions rely on the host's view
wherever one exists, because output from inside the sandbox can be forged. Here the
payloads are our own, so their output is useful for detail.
"""

import hashlib
import os
import pathlib
import shutil
import subprocess
import tempfile
import threading
import time

import pytest
import sandbox as sb

MIB = 1024**2


def run_python(box, code: str, timeout: int = 60) -> sb.ExecResult:
    return box.exec(["python", "-"], stdin=code.encode(), timeout=timeout)


class HostWatch:
    """Measures host responsiveness during an attack, from outside the sandbox."""

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


def test_sec01_runtime_is_gvisor_from_the_host(box):
    assert box.attestation["HostConfig"]["Runtime"] == "runsc"
    assert sb.attest(box.inspect()) == []


def test_sec02_host_secrets_do_not_reach_the_sandbox(image, monkeypatch):
    canary = "REPROFIX_CANARY_" + os.urandom(16).hex()
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", canary)
    monkeypatch.setenv("GITHUB_TOKEN", canary)
    with tempfile.NamedTemporaryFile("w", suffix=".canary", delete=False) as host_file:
        host_file.write(canary)
    try:
        hunter = f"""
import glob, os
found = []
print(dict(os.environ))
for path in glob.glob('/proc/*/environ'):
    try:
        print(path, open(path, 'rb').read())
    except OSError as error:
        print(path, error)
print('host file visible:', os.path.exists({host_file.name!r}))
for root, dirs, files in os.walk('/'):
    dirs[:] = [d for d in dirs if d not in ('proc', 'sys', 'opt', 'usr', 'lib')]
    for name in files:
        try:
            with open(os.path.join(root, name), 'rb') as handle:
                if {canary!r}.encode() in handle.read(1048576):
                    print('CANARY FOUND IN', root, name)
        except OSError:
            pass
"""
        with sb.Sandbox(image) as box:
            config_env = box.attestation["Config"]["Env"]
            result = run_python(box, hunter)
        assert result.exit_code == 0, result.text[-800:]
        assert "host file visible: False" in result.text
        # The hunter's own source holds the canary, so it can't be searched for in stderr tracebacks.
        assert canary not in " ".join(config_env)
        assert "CANARY FOUND" not in result.text
        environ_lines = [line for line in result.text.splitlines() if line.startswith(("{", "/proc/"))]
        assert canary not in "\n".join(line for line in environ_lines if "cmdline" not in line)
    finally:
        os.unlink(host_file.name)


def test_sec03_deletion_cannot_touch_the_image_or_host(box):
    with tempfile.NamedTemporaryFile("w", suffix=".canary", delete=False) as host_file:
        host_file.write(os.urandom(32).hex())
    before = hashlib.sha256(pathlib.Path(host_file.name).read_bytes()).hexdigest()
    try:
        box.exec(["sh", "-c", "rm -rf --no-preserve-root / 2>&1 | tail -5"], timeout=60)
        writes = box.exec(["sh", "-c", "touch /etc/reprofix-test; echo status=$?; touch /usr/reprofix-test; echo status=$?"])
        assert writes.text.count("status=1") == 2, writes.text
        still_there = box.exec(["python", "-c", "print('alive')"])
        assert still_there.stdout.strip() == b"alive"
        assert hashlib.sha256(pathlib.Path(host_file.name).read_bytes()).hexdigest() == before
        assert pathlib.Path("/etc/passwd").exists()
    finally:
        os.unlink(host_file.name)


def test_sec04_fork_bomb_hits_the_pid_limit_and_the_host_stays_responsive(box):
    bomb = """
import os, sys, time
count = 0
try:
    while count < 20000:
        pid = os.fork()
        if pid == 0:
            time.sleep(30)
            os._exit(0)
        count += 1
except OSError as error:
    print('fork failed after', count, error, flush=True)
    os._exit(0)
print('no limit reached', count, flush=True)
"""
    # gVisor's own threads count toward the same cgroup limit, so the kernel refusing a
    # fork can take the whole sandbox down instead of failing fork() inside it. Any of
    # the three endings is contained, but only with host-side proof the limit fired.
    cgroup = f"/sys/fs/cgroup/system.slice/docker-{box.attestation['Id']}.scope"
    with open(f"{cgroup}/pids.max") as handle:
        assert int(handle.read()) == box.profile.pids
    limit_hits = [0]
    done = threading.Event()

    def watch_limit() -> None:
        while not done.is_set():
            try:
                with open(f"{cgroup}/pids.events") as handle:
                    limit_hits[0] = max(limit_hits[0], int(handle.read().split()[1]))
            except OSError:
                pass  # The scope disappears when the sandbox dies.
            time.sleep(0.02)

    watcher = threading.Thread(target=watch_limit, daemon=True)
    watcher.start()
    with HostWatch() as watch:
        result = run_python(box, bomb, timeout=60)
    done.set()
    watcher.join()

    assert "no limit reached" not in result.text, result.text[-500:]
    assert limit_hits[0] > 0, "the host cgroup never recorded the PID limit firing"
    sandbox_died = result.exit_code == 128 and "WaitPID" in result.text
    fork_failed = "fork failed after" in result.text
    assert result.timed_out or fork_failed or sandbox_died, result.text[-500:]
    if fork_failed:
        count = int(result.text.split("fork failed after")[1].split()[0])
        assert count <= box.profile.pids
    if sandbox_died:
        # Docker can still report "running" here, so only a failed exec proves it's dead.
        assert box.exec(["true"], timeout=20).exit_code != 0
    assert watch.worst_gap_s < 5, f"host stalled for {watch.worst_gap_s:.1f}s"


def test_sec05_memory_bomb_is_stopped_near_the_limit(box):
    bomb = """
blocks = []
reached = 0
try:
    while reached < 6144:
        blocks.append(bytearray(b'x' * (64 * 1024 * 1024)))
        reached += 64
        print(reached, flush=True)
    print('no limit reached', flush=True)
except MemoryError:
    print('memory error at', reached, flush=True)
"""
    with HostWatch() as watch:
        result = run_python(box, bomb, timeout=90)
    assert "no limit reached" not in result.text, result.text[-300:]
    numbers = [int(line) for line in result.stdout.decode(errors="replace").splitlines() if line.strip().isdigit()]
    limit_mib = box.profile.memory_bytes // MIB
    assert numbers and max(numbers) <= limit_mib + 128, f"reached {max(numbers, default=0)} MiB"
    assert watch.worst_gap_s < 5, f"host stalled for {watch.worst_gap_s:.1f}s"


def test_sec06_infinite_loop_is_killed_from_the_host(box):
    started = time.monotonic()
    result = run_python(box, "while True: pass", timeout=5)
    elapsed = time.monotonic() - started
    assert result.timed_out
    assert elapsed < 20, f"kill took {elapsed:.1f}s"
    assert box.state()["Running"] is False


def test_sec06b_killing_cannot_be_ignored_by_trapping_signals(box):
    stubborn = """
import signal, time
for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(number, signal.SIG_IGN)
while True:
    time.sleep(0.1)
"""
    result = run_python(box, stubborn, timeout=5)
    assert result.timed_out
    assert box.state()["Running"] is False


@pytest.mark.parametrize("path,limit_mib", [("/workspace", 512), ("/tmp", 256)])
def test_sec07_disk_fill_stops_at_the_limit_and_host_is_unchanged(box, path, limit_mib):
    free_before = shutil.disk_usage("/").free
    result = box.exec(
        ["sh", "-c", f"dd if=/dev/zero of={path}/fill bs=1M count={limit_mib * 3} 2>&1; stat -c %s {path}/fill"],
        timeout=90,
    )
    assert "No space left" in result.text, result.text[-400:]
    written = int(result.stdout.decode().strip().splitlines()[-1])
    assert written <= (limit_mib + 1) * MIB
    assert abs(shutil.disk_usage("/").free - free_before) < 100 * MIB


def test_sec07b_read_only_root_cannot_be_used_for_disk_fill(box):
    result = box.exec(["sh", "-c", "dd if=/dev/zero of=/fill bs=1M count=10 2>&1; echo status=$?"])
    assert "status=1" in result.text or "Read-only" in result.text


def test_sec08_network_is_unreachable(box):
    assert box.attestation["HostConfig"]["NetworkMode"] == "none"
    bridge = subprocess.run(
        ["docker", "network", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}"],
        capture_output=True, text=True, env=sb.client_env(),
    )
    targets = ["203.0.113.10", "169.254.169.254"]
    if bridge.returncode == 0 and bridge.stdout.strip():
        targets.append(bridge.stdout.strip())
    probe = f"""
import socket
for host in {targets!r}:
    for port in (80, 443, 2375, 22):
        s = socket.socket()
        s.settimeout(3)
        try:
            s.connect((host, port))
            print('CONNECTED', host, port)
        except OSError as error:
            print('blocked', host, port, error)
        finally:
            s.close()
try:
    print('DNS RESOLVED', socket.gethostbyname('example.com'))
except OSError as error:
    print('dns blocked', error)
print('interfaces', [name for _, name in socket.if_nameindex()])
"""
    result = run_python(box, probe, timeout=90)
    assert result.exit_code == 0, result.text[-500:]
    assert "CONNECTED" not in result.text
    assert "DNS RESOLVED" not in result.text
    interfaces_line = next(line for line in result.text.splitlines() if line.startswith("interfaces"))
    assert "eth" not in interfaces_line


def test_sec10_no_docker_socket_no_capabilities_not_root(box):
    assert box.exec(["sh", "-c", "test -e /var/run/docker.sock; echo $?"]).stdout.strip() == b"1"
    assert box.exec(["id", "-u"]).stdout.strip() == b"65534"
    status = box.exec(["cat", "/proc/self/status"]).text
    effective = next(line for line in status.splitlines() if line.startswith("CapEff"))
    assert int(effective.split()[1], 16) == 0, effective
    no_new_privs = next(line for line in status.splitlines() if line.startswith("NoNewPrivs"))
    assert no_new_privs.split()[1] == "1", no_new_privs


def test_sec13_api_user_has_no_docker_access():
    groups = subprocess.run(["id", "-nG", "rfapi"], capture_output=True, text=True)
    assert groups.returncode == 0, "rfapi does not exist. Run deploy/setup-host.sh."
    assert "docker" not in groups.stdout.split()


def test_sec17_nothing_persists_between_sandboxes(image):
    marker = os.urandom(8).hex()
    with sb.Sandbox(image) as first:
        written = first.exec(["sh", "-c", f"echo {marker} > /workspace/m && echo {marker} > /tmp/m && cat /workspace/m"])
        assert written.stdout.strip().decode() == marker
    assert not sb.exists(first.run_id)
    with sb.Sandbox(image) as second:
        result = second.exec(["sh", "-c", "cat /workspace/m /tmp/m 2>&1; ls -A /workspace /tmp"])
    assert marker not in result.text


def test_output_flood_is_capped_and_host_memory_is_safe(box):
    result = box.exec(["sh", "-c", "head -c 200000000 /dev/zero"], timeout=60)
    assert len(result.stdout) == box.profile.max_stream_bytes
    assert result.stdout_truncated


def test_failed_attestation_destroys_the_container(image, monkeypatch):
    monkeypatch.setattr(sb, "attest", lambda *_a, **_k: ["forced mismatch"])
    created = sb.Sandbox(image)
    with pytest.raises(sb.AttestationError):
        created.__enter__()
    assert not sb.exists(created.run_id)
