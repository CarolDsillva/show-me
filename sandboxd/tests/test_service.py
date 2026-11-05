"""SEC-12 at the request layer: callers can't set limits, mounts, env or images. No Docker here."""

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import drills  # noqa: E402
import service  # noqa: E402

client = TestClient(service.app)  # Not used as a context manager, so nothing touches Docker.
GOOD_RUN = {
    "run_key": "k", "profile": "browser-session", "target_ref": "baseline", "kind": "replay",
    "script": "print(1)", "script_sha256": service.sha256(b"print(1)"),
}


@pytest.mark.parametrize("extra", [
    {"memory": "64g"}, {"image": "evil"}, {"env": {"A": "b"}}, {"mounts": ["/:/host"]}, {"runtime": "runc"}, {"pids": 99999},
])
def test_runs_reject_any_extra_field(extra):
    assert client.post("/v1/runs", json={**GOOD_RUN, **extra}).status_code == 422


@pytest.mark.parametrize("extra", [{"memory": "64g"}, {"image": "evil"}])
def test_sessions_reject_any_extra_field(extra):
    body = {"session_key": "k", "profile": "browser-session", "target_ref": "baseline", **extra}
    assert client.post("/v1/sessions", json=body).status_code == 422


def test_changed_script_is_refused_before_anything_runs():
    body = {**GOOD_RUN, "script": "print(2)"}
    assert client.post("/v1/runs", json=body).status_code == 422


@pytest.mark.parametrize("action", [
    {"action": "goto", "path": "http://203.0.113.10/"},
    {"action": "goto", "path": "//203.0.113.10/"},
    {"action": "run_shell", "value": "id"},
    {"action": "click", "selector": "a", "script": "x"},
])
def test_browser_actions_outside_the_allowed_list_are_refused(action):
    assert client.post("/v1/sessions/none/actions", json=action).status_code == 422


def test_action_missing_a_required_field_is_refused():
    assert client.post("/v1/sessions/none/actions", json={"action": "click"}).status_code == 422


def test_action_on_an_unknown_session_conflicts():
    assert client.post("/v1/sessions/none/actions", json={"action": "read_text"}).status_code == 409


def test_screenshots_that_are_not_png_are_dropped():
    reply = service.shape_action_reply({"ok": True, "screenshot_b64": "PHNjcmlwdD4=", "text": "x" * 9000, "elements": "nope"})
    assert reply["screenshot_b64"] is None and len(reply["text"]) == 4000 and reply["elements"] == []


def test_regression_run_cannot_choose_a_script():
    body = {**GOOD_RUN, "kind": "regression"}
    assert client.post("/v1/runs", json=body).status_code == 422


def test_replay_needs_a_script():
    body = {k: v for k, v in GOOD_RUN.items() if k not in ("script", "script_sha256")}
    assert client.post("/v1/runs", json=body).status_code == 422


def test_failed_test_names_are_filtered_and_capped():
    report = b"FAILED bookmarks/tests/test_a.py::T::test_x - boom\nERROR bookmarks/tests/test_b.py\nFAILED <script>\nfine\n"
    assert service.parse_failed_tests(report) == ["bookmarks/tests/test_a.py::T::test_x", "bookmarks/tests/test_b.py"]


def test_drills_accept_only_the_fixed_names_and_no_payload():
    base = {"run_key": "k", "profile": "browser-session", "target_ref": "baseline", "kind": "drill"}
    assert client.post("/v1/runs", json=base).status_code == 422
    assert client.post("/v1/runs", json={**base, "drill": "mine"}).status_code == 422
    assert client.post("/v1/runs", json={**base, "drill": "fork", "script": "x"}).status_code == 422
    assert client.post("/v1/runs", json={**base, "drill": "fork", "patch": "x"}).status_code == 422


def result(exit_code=0, timed_out=False, duration=1.0):
    return drills.sb.ExecResult(exit_code, b"", b"", False, False, timed_out, duration)


def evaluate(drill, output, **changes):
    arguments = {
        "output": output, "result": result(), "oom_killed": False, "alive_after": True, "decoy": "DECOY",
        "host_secret": "HOSTSECRET", "pid_hits": 0, "disk_drop": 0, "profile": drills.sb.BROWSER_SESSION, **changes,
    }
    return [check.passed for check in drills.evaluate(drill, **arguments)]


def test_secrets_drill_fails_if_the_host_secret_appears_or_the_hunt_did_not_run():
    assert all(evaluate("secrets", "DECOY ABSENT DRILL DONE"))
    assert not all(evaluate("secrets", "DECOY HOSTSECRET DRILL DONE"))
    assert not all(evaluate("secrets", "DRILL DONE"))
    assert not all(evaluate("secrets", "DECOY REACHABLE /var/run/docker.sock DRILL DONE"))


def test_network_drill_fails_if_anything_connected_or_the_payload_did_not_finish():
    assert all(evaluate("network", "BLOCKED a DRILL DONE"))
    assert not all(evaluate("network", "REACHED a DRILL DONE"))
    assert not all(evaluate("network", "BLOCKED a"))


def test_delete_drill_fails_if_a_write_landed_or_the_sandbox_broke():
    assert all(evaluate("delete", "REFUSED DRILL DONE"))
    assert not all(evaluate("delete", "WROTE /etc/x DRILL DONE"))
    assert not all(evaluate("delete", "REFUSED DRILL DONE", alive_after=False))


def test_disk_drill_fails_without_a_limit_or_if_host_disk_dropped():
    assert all(evaluate("disk", "DISK LIMIT AT MIB 512 DRILL DONE"))
    assert not all(evaluate("disk", "NO LIMIT 4096 DRILL DONE"))
    assert not all(evaluate("disk", "DISK LIMIT AT MIB 512", disk_drop=500 * drills.sb.MIB))


def test_fork_drill_needs_host_proof_that_the_limit_fired():
    assert all(evaluate("fork", "FORKED 200", pid_hits=3))
    assert not all(evaluate("fork", "FORKED 200", pid_hits=0))
    assert not all(evaluate("fork", "NO LIMIT 20000", pid_hits=3))


def test_memory_drill_needs_the_process_to_be_stopped():
    assert all(evaluate("memory", "ALLOCATED MIB 1920", oom_killed=True))
    assert not all(evaluate("memory", "ALLOCATED MIB 64"))
    assert not all(evaluate("memory", "NO LIMIT", oom_killed=True))


def test_loop_drill_needs_the_host_to_kill_it_on_time():
    assert all(evaluate("loop", "", result=result(None, True, 15.2)))
    assert not all(evaluate("loop", "", result=result(0, False, 1.0)))
    assert not all(evaluate("loop", "", result=result(None, True, 60.0)))
