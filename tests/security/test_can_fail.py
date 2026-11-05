"""Proves the checks can fail: each drill runs with its protection deliberately weakened.

A check that passes under a weakened setting proves nothing, so every test here expects
"not contained", and expects attestation to have spotted the change. VM only. These run
real payloads, so the weakened limits still stop short of harming the VM. Network targets
are a reserved address and the VM's own metadata service, by TCP connect only.
"""

import pathlib

import drills
import pytest
import sandbox as sb

MEMORY_LIMIT = 6 * sb.GIB


def replace(args: list[str], prefix: str, new: str | None) -> list[str]:
    out = [new if arg.startswith(prefix) else arg for arg in args]
    return [arg for arg in out if arg is not None]


def bigger_memory(args):
    args = replace(args, "--memory-swap=", f"--memory-swap={MEMORY_LIMIT}")
    return replace(args, "--memory=", f"--memory={MEMORY_LIMIT}")


def bigger_workspace(args):
    args = bigger_memory(args)
    return [arg.replace("size=512m", "size=5000m") if arg.startswith("/workspace:") else arg for arg in args]


def writable_root_as_root(args):
    return replace(replace(args, "--read-only", None), "--user=", "--user=0:0")


def with_network(args):
    return replace(args, "--network=", "--network=bridge")


def no_pid_limit(args):
    return replace(args, "--pids-limit=", "--pids-limit=100000")


def plant_secret(args):
    index = args.index("--env")
    return [*args[:index], "--env", "PLANTED=REPROFIX_HOST_SECRET_FOR_TEST", *args[index:]]


@pytest.fixture
def weaken(monkeypatch):
    """Weaken the container, and record what attestation says instead of stopping on it."""
    seen: list[list[str]] = []
    real_args, real_attest = sb.docker_run_args, sb.attest

    def install(change):
        monkeypatch.setattr(sb, "docker_run_args", lambda *a, **k: change(real_args(*a, **k)))

        def recording_attest(*a, **k):
            problems = real_attest(*a, **k)
            seen.append(problems)
            return []

        monkeypatch.setattr(sb, "attest", recording_attest)
        return seen

    return install


def view(_inspect, problems):
    return {"runtime": "runsc", "network_mode": "weakened", "mismatches": problems}


def failed_names(result):
    return [check["name"] for check in result["checks"] if not check["passed"]]


@pytest.mark.parametrize("drill,change,expected_failure,attested", [
    ("memory", bigger_memory, "Stopped at the memory limit", "Memory"),
    ("disk", bigger_workspace, "Writing stopped at the workspace limit", "Tmpfs /workspace"),
    ("delete", writable_root_as_root, "Writes to /etc and /usr refused", "ReadonlyRootfs"),
    ("network", with_network, "All connections failed", "NetworkMode"),
    ("fork", no_pid_limit, "The kernel refused forks at the limit", "PidsLimit"),
])
def test_drill_reports_not_contained_when_the_protection_is_removed(weaken, image, tmp_path, drill, change, expected_failure, attested):
    seen = weaken(change)
    canary = pathlib.Path(tmp_path / "canary")
    canary.write_text("host canary")
    result = drills.run(drill, image, canary, view)
    assert result["destruction"]["destroyed"] is True
    assert result["contained"] is False, result
    assert expected_failure in failed_names(result), (failed_names(result), result.get("output_tail", "")[-300:])
    assert any(attested in problem for problems in seen for problem in problems), seen


def test_secrets_drill_reports_a_secret_that_reaches_the_sandbox(weaken, image, tmp_path):
    weaken(plant_secret)
    canary = pathlib.Path(tmp_path / "canary")
    canary.write_text("REPROFIX_HOST_SECRET_FOR_TEST")
    result = drills.run("secrets", image, canary, view)
    assert result["contained"] is False, result
    assert "Host canary absent from all output" in failed_names(result), failed_names(result)


def test_attestation_stops_a_weakened_container_when_not_bypassed(image, monkeypatch):
    real_args = sb.docker_run_args
    monkeypatch.setattr(sb, "docker_run_args", lambda *a, **k: with_network(real_args(*a, **k)))
    box = sb.Sandbox(image)
    with pytest.raises(sb.AttestationError):
        box.__enter__()
    assert box.destroyed and not sb.exists(box.run_id)
