"""Can a patch fake the regression result? VM only, through the live sandboxd.

The patch here exits the test process with success at import time, which is what a malicious fix
would try. The built-in failing test must then not report as failed, so the run can't count.
"""

import difflib
import subprocess
import uuid

import sandbox as sb
from test_live_service import call


def malicious_patch(image: str) -> str:
    result = subprocess.run(
        ["docker", "run", "--rm", "--network=none", "--runtime=runsc", image, "cat", "/opt/linkding/bookmarks/utils.py"],
        capture_output=True, timeout=120, env=sb.client_env(),
    )
    assert result.returncode == 0, result.stderr[-300:]
    before = result.stdout.decode()
    after = "import os; os._exit(0)\n" + before
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True), "a/bookmarks/utils.py", "b/bookmarks/utils.py",
    ))


def regression(patch: str | None = None) -> dict:
    body = {"run_key": f"sec-{uuid.uuid4().hex}", "profile": "browser-session", "target_ref": "baseline", "kind": "regression"}
    if patch:
        body["patch"] = patch
    status, reply = call("POST", "/v1/runs", body)
    assert status == 200, reply
    assert reply["destruction"]["destroyed"] is True
    return reply


def test_a_patch_that_exits_with_success_cannot_look_like_a_clean_run(image):
    reply = regression(malicious_patch(image))
    assert reply["applied_patch_sha256"]
    assert "patch_failed" not in reply, reply.get("error")
    assert reply["canary_failed"] is not True, reply.get("stdout", "")[-400:]


def test_the_unpatched_code_reports_the_built_in_failure_and_the_full_count():
    reply = regression()
    assert reply["canary_failed"] is True, reply.get("stdout", "")[-400:]
    assert reply["exit_code"] == 1
    assert reply["tests_failed"] >= 1 and reply["tests_passed"] > 100
    assert not any("canary" in name for name in reply["failed_tests"])
