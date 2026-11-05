"""SEC-12 at the request layer: callers can't set limits, mounts, env or images. No Docker here."""

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

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
