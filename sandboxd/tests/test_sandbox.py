"""Unit tests for the profile and attestation. These run anywhere, with no Docker.

Passing here says nothing about containment. Only tests/security on the VM can show that.
"""

import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sandbox as sb  # noqa: E402


def good_inspect() -> dict:
    profile = sb.BROWSER_SESSION
    return {
        "HostConfig": {
            "Runtime": "runsc",
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "Memory": profile.memory_bytes,
            "MemorySwap": profile.memory_bytes,
            "NanoCpus": profile.nano_cpus,
            "PidsLimit": profile.pids,
            "LogConfig": {"Type": "none"},
            "Binds": None,
            "PidMode": "",
            "IpcMode": "private",
            "SecurityOpt": ["no-new-privileges"],
            "Tmpfs": {
                "/workspace": "rw,nosuid,nodev,size=512m,uid=65534,gid=65534,mode=0700",
                "/tmp": "rw,nosuid,nodev,size=256m,uid=65534,gid=65534,mode=0700",
            },
        },
        "Config": {"User": sb.SANDBOX_USER, "Env": ["HOME=/tmp", "PATH=/usr/bin"]},
        "Mounts": [],
    }


def test_good_container_passes_attestation():
    assert sb.attest(good_inspect()) == []


def mutated(path: tuple[str, str], value: object) -> list[str]:
    inspect = good_inspect()
    inspect[path[0]][path[1]] = value
    return sb.attest(inspect)


def test_each_weakened_setting_is_caught():
    weakened = [
        (("HostConfig", "Runtime"), "runc"),
        (("HostConfig", "NetworkMode"), "bridge"),
        (("HostConfig", "ReadonlyRootfs"), False),
        (("HostConfig", "Privileged"), True),
        (("HostConfig", "CapDrop"), []),
        (("HostConfig", "CapAdd"), ["SYS_ADMIN"]),
        (("HostConfig", "Memory"), 0),
        (("HostConfig", "MemorySwap"), -1),
        (("HostConfig", "NanoCpus"), 0),
        (("HostConfig", "PidsLimit"), 0),
        (("HostConfig", "LogConfig"), {"Type": "json-file"}),
        (("HostConfig", "Binds"), ["/var/run/docker.sock:/var/run/docker.sock"]),
        (("HostConfig", "PidMode"), "host"),
        (("HostConfig", "IpcMode"), "host"),
        (("HostConfig", "SecurityOpt"), []),
        (("HostConfig", "Tmpfs"), {}),
        (("Config", "User"), "root"),
    ]
    for path, value in weakened:
        assert mutated(path, value), f"{path} = {value!r} was not caught"


def test_host_mount_is_caught():
    inspect = good_inspect()
    inspect["Mounts"] = [{"Type": "bind", "Source": "/"}]
    assert sb.attest(inspect)


def test_credential_shaped_env_is_caught():
    for name in ("VULTR_INFERENCE_API_KEY", "GITHUB_TOKEN", "OPERATOR_PASSWORD", "some_secret"):
        inspect = good_inspect()
        inspect["Config"]["Env"].append(f"{name}=x")
        assert sb.attest(inspect), name


def test_run_args_match_the_profile():
    args = sb.docker_run_args("abc", "sha256:image", 123)
    for flag in (
        "--runtime=runsc", "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--user=65534:65534", "--log-driver=none",
        f"--memory={sb.GIB * 2}", f"--memory-swap={sb.GIB * 2}", "--cpus=2", "--pids-limit=512",
    ):
        assert flag in args, flag
    assert "--privileged" not in args
    assert not any(a in ("-v", "--volume", "--mount") for a in args)
    assert args[-3:] == ["sha256:image", "sleep", "infinity"]


def test_run_args_only_pass_allowlisted_env():
    args = sb.docker_run_args("abc", "img", 1)
    passed = [args[i + 1].split("=")[0] for i, a in enumerate(args) if a == "--env"]
    assert sorted(passed) == sorted(sb.ENV_ALLOWLIST)


def test_client_env_drops_everything_else(monkeypatch):
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "nope")
    assert "VULTR_INFERENCE_API_KEY" not in sb.client_env()


def test_read_capped_keeps_the_limit_and_flags_truncation():
    kept, truncated = sb.read_capped(io.BytesIO(b"x" * 200_000), 100_000)
    assert len(kept) == 100_000 and truncated


def test_read_capped_exact_fit_is_not_truncated():
    kept, truncated = sb.read_capped(io.BytesIO(b"abc"), 3)
    assert kept == b"abc" and not truncated
