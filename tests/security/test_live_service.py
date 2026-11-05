"""Tests against the deployed sandboxd over its real Unix socket. VM only.

SEC-11, SEC-12, SEC-18, SEC-20, and the seven containment drills. A failure to reach the
service fails the test. Nothing here skips.
"""

import hashlib
import http.client
import json
import socket
import subprocess
import time
import uuid

import pytest
import sandbox as sb

SOCKET = "/run/reprofix/sandboxd.sock"
REAP_WAIT_S = 45


class UnixConnection(http.client.HTTPConnection):
    def __init__(self) -> None:
        super().__init__("sandboxd", timeout=300)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(SOCKET)


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    connection = UnixConnection()
    try:
        connection.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read()
    finally:
        connection.close()
    return response.status, json.loads(raw) if raw else {}


def run_body(**changes) -> dict:
    script = "print(1)"
    return {
        "run_key": f"sec-{uuid.uuid4().hex}", "profile": "browser-session", "target_ref": "baseline", "kind": "replay",
        "script": script, "script_sha256": hashlib.sha256(script.encode()).hexdigest(), **changes,
    }


@pytest.mark.parametrize("extra", [
    {"memory": "64g"}, {"image": "alpine"}, {"env": {"A": "b"}}, {"mounts": ["/:/host"]}, {"runtime": "runc"},
    {"network": "host"}, {"privileged": True}, {"pids": 99999},
])
def test_sec12_runs_reject_attempts_to_change_the_sandbox(extra):
    before = sb.labeled_containers()
    assert call("POST", "/v1/runs", run_body(**extra))[0] == 422
    assert sb.labeled_containers() == before


@pytest.mark.parametrize("field,value", [("profile", "root"), ("target_ref", "main"), ("kind", "shell")])
def test_sec12_unknown_profile_target_or_kind_is_rejected(field, value):
    assert call("POST", "/v1/runs", run_body(**{field: value}))[0] == 422


def test_sec12_sessions_reject_attempts_to_change_the_sandbox():
    body = {"session_key": "sec", "profile": "browser-session", "target_ref": "baseline", "image": "alpine"}
    assert call("POST", "/v1/sessions", body)[0] == 422


def test_sec18_a_changed_script_is_refused_before_a_container_starts():
    before = sb.labeled_containers()
    body = run_body()
    body["script"] += "\nprint('tampered')"
    status, reply = call("POST", "/v1/runs", body)
    assert status == 422, reply
    assert sb.labeled_containers() == before


def test_drill_payload_cannot_be_supplied():
    assert call("POST", "/v1/runs", {
        "run_key": f"sec-{uuid.uuid4().hex}", "profile": "browser-session", "target_ref": "baseline",
        "kind": "drill", "drill": "fork", "script": "print(1)",
    })[0] == 422
    assert call("POST", "/v1/runs", {
        "run_key": f"sec-{uuid.uuid4().hex}", "profile": "browser-session", "target_ref": "baseline",
        "kind": "drill", "drill": "my-own-payload",
    })[0] == 422


@pytest.fixture
def session():
    status, reply = call("POST", "/v1/sessions", {
        "session_key": f"sec-{uuid.uuid4().hex}", "profile": "browser-session", "target_ref": "baseline",
    })
    assert status == 201, reply
    yield reply
    status, destruction = call("DELETE", f"/v1/sessions/{reply['session_id']}")
    assert status == 200 and destruction["destroyed"] is True
    assert sb.labeled_containers() == []


def test_session_is_attested_and_logged_in(session):
    attestation = session["attestation"]
    assert attestation["runtime"] == "runsc" and attestation["network_mode"] == "none"
    assert attestation["mismatches"] == []
    assert session["initial"]["ok"] is True and "/bookmarks" in session["initial"]["url"]


@pytest.mark.parametrize("action", [
    {"action": "goto", "path": "http://203.0.113.10/"},
    {"action": "goto", "path": "//203.0.113.10/"},
    {"action": "goto", "path": "https://example.invalid/"},
    {"action": "evaluate", "value": "fetch('http://203.0.113.10/')"},
    {"action": "shell", "value": "id"},
    {"action": "click", "selector": "a", "script": "x"},
])
def test_sec20_actions_outside_the_allowed_list_are_refused(session, action):
    assert call("POST", f"/v1/sessions/{session['session_id']}/actions", action)[0] == 422


def test_sec20_a_page_cannot_reach_outside_through_the_browser(session):
    # A real link to a reserved address, followed through the browser. It must not load.
    session_id = session["session_id"]
    status, reply = call("POST", f"/v1/sessions/{session_id}/actions", {"action": "read_text"})
    assert status == 200 and "203.0.113.10" not in reply["url"]
    script = (
        "import sys\nfrom playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p:\n"
        "  b = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-gpu'])\n"
        "  page = b.new_page()\n"
        "  try:\n"
        "    page.goto('http://203.0.113.10/', timeout=5000)\n"
        "    print('REACHED')\n"
        "  except Exception as error:\n"
        "    print('BLOCKED', type(error).__name__)\n"
        "  b.close()\n"
    )
    name = sb.labeled_containers()[0]
    result = subprocess.run(["docker", "exec", "-i", name, "python", "-"], input=script.encode(), capture_output=True, timeout=60, env=sb.client_env())
    assert b"BLOCKED" in result.stdout and b"REACHED" not in result.stdout


def test_sec11_the_reaper_removes_a_container_past_its_deadline(image):
    run_id = uuid.uuid4().hex[:16]
    args = sb.docker_run_args(run_id, image, int(time.time()) - 60)
    assert subprocess.run(args, capture_output=True, timeout=120, env=sb.client_env()).returncode == 0
    deadline = time.time() + REAP_WAIT_S
    while time.time() < deadline and sb.exists(run_id):
        time.sleep(1)
    gone = not sb.exists(run_id)
    if not gone:
        sb.remove_container(sb.container_name(run_id))
    assert gone, "The reaper didn't remove the overdue container"


def test_host_telemetry_comes_from_outside_the_sandbox():
    status, reply = call("GET", "/v1/host")
    assert status == 200
    assert len(reply["canary_sha256"]) == 64 and reply["disk_free_bytes"] > 0 and reply["running_sandboxes"] == 0


@pytest.mark.parametrize("drill", ["secrets", "network", "delete", "disk", "fork", "memory", "loop"])
def test_containment_drill_is_contained(drill):
    status, reply = call("POST", "/v1/runs", {
        "run_key": f"sec-{uuid.uuid4().hex}", "profile": "browser-session", "target_ref": "baseline",
        "kind": "drill", "drill": drill,
    })
    assert status == 200, reply
    failed = [check for check in reply["checks"] if not check["passed"]]
    assert reply["contained"] is True, (failed, reply.get("error"), reply.get("output_tail", "")[-400:])
    assert reply["destruction"]["destroyed"] is True
    assert sb.labeled_containers() == []
