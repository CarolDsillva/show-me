"""sandboxd: the only service that can reach Docker. It holds no secrets.

Callers name a profile. They can't set limits, images, mounts or env, and unknown fields
are rejected. Everything that comes out of a sandbox is capped and treated as hostile.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import datetime as dt
import hashlib
import json
import os
import pathlib
import queue
import re
import shutil
import threading
import time
import uuid
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import drills
import sandbox as sb
import target

IMAGE = os.environ.get("REPROFIX_IMAGE", "")
STATE_DIR = pathlib.Path(os.environ.get("STATE_DIRECTORY", "/var/lib/reprofix-sandboxd"))
CANARY_PATH = pathlib.Path(os.environ.get("SANDBOXD_CANARY", "/srv/reprofix/canary"))
MAX_SANDBOXES = 2
MAX_DRIVER_LINE = 4 * sb.MIB
ACTION_TIMEOUT_S = 45
DRIVER_START_TIMEOUT_S = 90
REAP_INTERVAL_S = 15
SYMPTOM_EXIT = 42
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SHA256 = r"^[a-f0-9]{64}$"
KEY = r"^[A-Za-z0-9_.:-]{1,128}$"
MAX_REPORTED_FAILURES = 200
REGRESSION_TIMEOUT_S = 420
# A fixed command. No request can choose what runs here.
# The full suite takes about 25 minutes under gVisor, so this is a subset around the UI we patch.
REGRESSION_TESTS = (
    "test_tags_index_view", "test_tags_edit_view", "test_tags_new_view", "test_tags_merge_view",
    "test_tag_cloud_template", "test_bookmarks_list_template", "test_bookmark_search_tag",
    "test_pagination_tag", "test_tags_model",
)
REGRESSION_COMMAND = (
    "python -m pytest " + " ".join(f"bookmarks/tests/{name}.py" for name in REGRESSION_TESTS)
    + " -q -n 2 -p no:cacheprovider --no-header --tb=no -rfE"
)

@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", IMAGE):
        raise RuntimeError("REPROFIX_IMAGE must be an image ID")
    # Sessions don't survive a restart, so anything still running here is orphaned.
    for name in sb.labeled_containers():
        sb.remove_container(name)
    threading.Thread(target=reaper_loop, daemon=True).start()
    yield


app = FastAPI(title="sandboxd", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
capacity = threading.BoundedSemaphore(MAX_SANDBOXES)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionRequest(Strict):
    session_key: str = Field(pattern=KEY)
    profile: Literal["browser-session"]
    target_ref: Literal["baseline"]


class BrowserAction(Strict):
    action: Literal["goto", "click", "fill", "press", "select", "wait_for", "screenshot", "read_text"]
    # A path on the sandbox's own app. A scheme, host or "//" can't match.
    path: str | None = Field(default=None, max_length=512, pattern=r"^/([^/\\\s][^\\\s]*)?$")
    selector: str | None = Field(default=None, max_length=512)
    value: str | None = Field(default=None, max_length=2000)


class RunRequest(Strict):
    run_key: str = Field(pattern=KEY)
    profile: Literal["browser-session"]
    target_ref: Literal["baseline"]
    kind: Literal["replay", "regression", "drill"]
    drill: Literal["secrets", "network", "delete", "disk", "fork", "memory", "loop"] | None = None
    script: str | None = Field(default=None, max_length=200_000)
    script_sha256: str | None = Field(default=None, pattern=SHA256)
    patch: str | None = Field(default=None, max_length=200_000)


REQUIRED_FIELDS = {
    "goto": ("path",), "click": ("selector",), "fill": ("selector", "value"), "press": ("selector", "value"),
    "select": ("selector", "value"), "wait_for": ("selector",), "screenshot": (), "read_text": (),
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def attestation_view(inspect: dict, problems: list[str]) -> dict:
    host = inspect.get("HostConfig", {})
    return {
        "runtime": host.get("Runtime"),
        "network_mode": host.get("NetworkMode"),
        "read_only": host.get("ReadonlyRootfs"),
        "memory_bytes": host.get("Memory"),
        "nano_cpus": host.get("NanoCpus"),
        "pids_limit": host.get("PidsLimit"),
        "image_id": inspect.get("Image"),
        "mismatches": problems,
    }


def clean_png(encoded: object) -> bytes | None:
    """Decode a screenshot from the sandbox. Anything that isn't a PNG is dropped."""
    if not isinstance(encoded, str):
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    return data if data.startswith(PNG_MAGIC) else None


def shape_action_reply(reply: dict) -> dict:
    """Keep known fields with known types, and cap them. The reply is hostile."""
    def text(name: str, limit: int) -> str:
        value = reply.get(name)
        return value[:limit] if isinstance(value, str) else ""

    png = clean_png(reply.get("screenshot_b64"))
    elements = []
    raw_elements = reply.get("elements")
    for item in raw_elements[:60] if isinstance(raw_elements, list) else []:
        if isinstance(item, dict):
            elements.append({
                key: item[key][:300] for key in ("tag", "text", "href", "type", "selector")
                if isinstance(item.get(key), str)
            })
    console = reply.get("console")
    return {
        "ok": reply.get("ok") is True,
        "error": text("error", 600) or None,
        "url": text("url", 2048),
        "title": text("title", 200),
        "text": text("text", 4000),
        "elements": elements,
        "console": [line[:300] for line in console[:10] if isinstance(line, str)] if isinstance(console, list) else [],
        "screenshot_b64": base64.b64encode(png).decode() if png else None,
        "screenshot_sha256": sha256(png) if png else None,
    }


class LiveSession:
    def __init__(self, session_id: str, box: sb.Sandbox, proc, deadline_epoch: int, attestation: dict) -> None:
        self.session_id = session_id
        self.box = box
        self.proc = proc
        self.deadline_epoch = deadline_epoch
        self.attestation = attestation
        self.lock = threading.Lock()
        self.ended = False
        self.lines: queue.Queue[bytes | None] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        try:
            while True:
                line = self.proc.stdout.readline(MAX_DRIVER_LINE + 1)
                if not line or len(line) > MAX_DRIVER_LINE:
                    break
                self.lines.put(line)
        finally:
            self.lines.put(None)

    def next_reply(self, timeout: float) -> dict | None:
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            return None
        if line is None:
            return None
        try:
            reply = json.loads(line)
        except (ValueError, RecursionError):
            return None
        return reply if isinstance(reply, dict) else None

    def send(self, payload: dict) -> bool:
        try:
            self.proc.stdin.write(json.dumps(payload).encode() + b"\n")
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False


sessions: dict[str, LiveSession] = {}
sessions_by_key: dict[str, str] = {}
registry_lock = threading.Lock()


def end_session(session: LiveSession) -> dict:
    """Destroy the container on every path. destroyed is true only if inspect says not found."""
    session.ended = True
    with contextlib.suppress(Exception):
        session.proc.kill()
    destroyed = False
    try:
        session.box.destroy()
        destroyed = True
    except Exception:
        destroyed = not sb.exists(session.box.run_id)
    capacity.release()
    with registry_lock:
        sessions.pop(session.session_id, None)
    return {"destroyed": destroyed, "confirmed_at": now_iso(), "name": session.box.name}


@app.post("/v1/sessions", status_code=201)
def create_session(request: SessionRequest) -> dict:
    with registry_lock:
        existing = sessions.get(sessions_by_key.get(request.session_key, ""))
    if existing:
        return {
            "session_id": existing.session_id, "deadline_epoch": existing.deadline_epoch,
            "attestation": existing.attestation, "initial": None,
        }
    if not capacity.acquire(blocking=False):
        raise HTTPException(503, "No sandbox capacity")
    box = sb.Sandbox(IMAGE)
    try:
        box.__enter__()
    except Exception as error:
        capacity.release()
        raise HTTPException(503, f"Sandbox unavailable: {type(error).__name__}") from error
    session_id = uuid.uuid4().hex
    proc = None
    try:
        login = target.new_login()
        target.prepare(box, login)
        proc = box.open_stream(["python", f"{target.RF_DIR}/browser_driver.py"])
        attestation = attestation_view(box.attestation, sb.attest(box.attestation))
        deadline = int(time.time()) + box.profile.session_timeout_s
        session = LiveSession(session_id, box, proc, deadline, attestation)
        if not session.send({"user": login.user, "password": login.password}):
            raise RuntimeError("driver refused login")
        reply = session.next_reply(DRIVER_START_TIMEOUT_S)
        if reply is None or reply.get("ok") is not True:
            raise RuntimeError("driver failed to start")
    except Exception as error:
        if proc:
            proc.kill()
        with contextlib.suppress(Exception):
            box.destroy()
        capacity.release()
        raise HTTPException(503, f"Sandbox unavailable: {type(error).__name__}: {str(error)[:300]}") from error
    with registry_lock:
        sessions[session_id] = session
        sessions_by_key[request.session_key] = session_id
    return {
        "session_id": session_id, "deadline_epoch": deadline, "attestation": attestation,
        "initial": shape_action_reply(reply),
    }


@app.post("/v1/sessions/{session_id}/actions")
def session_action(session_id: str, action: BrowserAction) -> dict:
    for field in REQUIRED_FIELDS[action.action]:
        if getattr(action, field) is None:
            raise HTTPException(422, f"{action.action} needs {field}")
    with registry_lock:
        session = sessions.get(session_id)
    if session is None or session.ended:
        raise HTTPException(409, "Session has ended")
    with session.lock:
        if time.time() > session.deadline_epoch:
            end_session(session)
            raise HTTPException(409, "Session passed its deadline")
        if session.send(action.model_dump(exclude_none=True)):
            reply = session.next_reply(ACTION_TIMEOUT_S)
            if reply is not None:
                return shape_action_reply(reply)
        # No reply means the driver or gVisor died, or it stopped answering. Fail closed.
        end_session(session)
        raise HTTPException(409, "Session ended: the sandbox stopped responding")


@app.delete("/v1/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    with registry_lock:
        session = sessions.get(session_id)
    if session is None:
        return {"destroyed": True, "confirmed_at": now_iso(), "already_ended": True}
    with session.lock:
        return end_session(session)


def run_path(run_key: str) -> pathlib.Path:
    return STATE_DIR / "runs" / f"{sha256(run_key.encode())}.json"


def parse_failed_tests(stdout: bytes) -> list[str]:
    """Test names from the runner's report. Capped, and only safe characters are kept."""
    failed = []
    for line in stdout.decode(errors="replace").splitlines():
        match = re.match(r"^(?:FAILED|ERROR) ([\w./:\-\[\]]{1,300})", line)
        if match:
            failed.append(match.group(1))
        if len(failed) >= MAX_REPORTED_FAILURES:
            break
    return sorted(set(failed))


def run_in_sandbox(request: RunRequest) -> dict:
    started_at = now_iso()
    result: dict = {
        "run_key": request.run_key, "kind": request.kind, "script_sha256": request.script_sha256,
        "applied_patch_sha256": sha256(request.patch.encode()) if request.patch else None,
        "exit_code": None, "symptom_observed": None, "termination": "sandbox_error", "artifacts": [],
        "started_at": started_at,
    }
    box = sb.Sandbox(IMAGE)
    try:
        with box:
            result["sandbox_id"] = box.name
            result["attestation"] = attestation_view(box.attestation, sb.attest(box.attestation))
            login = target.new_login()
            if request.kind == "regression":
                target.copy_app(box, request.patch)
                run = box.exec(["sh", "-c", f"cd /workspace/app && {REGRESSION_COMMAND}"], timeout=REGRESSION_TIMEOUT_S)
                result["failed_tests"] = parse_failed_tests(run.stdout)
            else:
                target.prepare(box, login, request.patch)
                target.load_files(box, {"repro.py": request.script.encode()})
                run = box.exec(
                    ["sh", "-c", f"cd {target.RF_DIR} && {' '.join(login.env())} python repro.py"],
                    timeout=box.profile.command_timeout_s,
                )
            result.update(
                exit_code=run.exit_code,
                stdout=run.stdout.decode(errors="replace"),
                stderr=run.stderr.decode(errors="replace")[-20000:],
                stdout_sha256=sha256(run.stdout),
                stderr_sha256=sha256(run.stderr),
                stdout_truncated=run.stdout_truncated,
                stderr_truncated=run.stderr_truncated,
                seconds=round(run.duration_s, 2),
            )
            state = box.state()
            if run.timed_out:
                result["termination"] = "timed_out"
            elif state.get("OOMKilled"):
                result["termination"] = "oom_killed"
            elif run.exit_code == 128 or not box.is_alive():
                result["termination"] = "sandbox_error"
            else:
                result["termination"] = "exited"
                if request.kind == "replay":
                    result["symptom_observed"] = run.exit_code == SYMPTOM_EXIT if run.exit_code in (0, SYMPTOM_EXIT) else None
                    shot = box.exec(["cat", "/tmp/final.png"], max_bytes=box.profile.max_artifact_bytes)
                    if shot.exit_code == 0 and shot.stdout.startswith(PNG_MAGIC) and not shot.stdout_truncated:
                        result["artifacts"].append({
                            "name": "final.png", "sha256": sha256(shot.stdout), "bytes": len(shot.stdout),
                            "content_b64": base64.b64encode(shot.stdout).decode(),
                        })
    except sb.AttestationError as error:
        result["error"] = f"Attestation failed: {str(error)[:500]}"
    except target.PatchError as error:
        result["termination"] = "exited"
        result["patch_failed"] = True
        result["error"] = str(error)[:800]
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {str(error)[:500]}"
    result["finished_at"] = now_iso()
    gone = box.destroyed or not sb.exists(box.run_id)
    result["destruction"] = {"destroyed": gone, "confirmed_at": now_iso(), "name": box.name}
    return result


@app.post("/v1/runs")
def create_run(request: RunRequest) -> dict:
    if request.kind == "replay":
        if request.script is None or request.script_sha256 is None:
            raise HTTPException(422, "A replay needs script and script_sha256")
        # Checked on the bytes that will run, so a changed script can never replay as the frozen one.
        if sha256(request.script.encode()) != request.script_sha256:
            raise HTTPException(422, "Script doesn't match script_sha256")
    elif request.script is not None or request.script_sha256 is not None or request.patch is not None:
        if request.kind == "drill" or request.script is not None or request.script_sha256 is not None:
            raise HTTPException(422, "This kind of run takes no script or patch")
    if request.kind == "drill" and request.drill is None:
        raise HTTPException(422, "A drill must name one of the fixed drills")
    if request.kind != "drill" and request.drill is not None:
        raise HTTPException(422, "Only a drill names a drill")
    path = run_path(request.run_key)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if not capacity.acquire(blocking=True, timeout=120):
        raise HTTPException(503, "No sandbox capacity")
    try:
        if request.kind == "drill":
            result = {"run_key": request.run_key, "kind": "drill", **drills.run(request.drill, IMAGE, CANARY_PATH, attestation_view)}
        else:
            result = run_in_sandbox(request)
    finally:
        capacity.release()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result), encoding="utf-8")
    temporary.replace(path)
    return result


@app.get("/v1/host")
def host_telemetry() -> dict:
    try:
        canary = sha256(CANARY_PATH.read_bytes())
    except OSError:
        canary = None
    return {
        "canary_sha256": canary,
        "disk_free_bytes": shutil.disk_usage("/").free,
        "load_1m": os.getloadavg()[0],
        "running_sandboxes": len(sb.labeled_containers()),
        "sampled_at": now_iso(),
    }


def reap_once() -> list[str]:
    """Remove anything past its deadline, including containers left by a crash."""
    removed = []
    for name in sb.overdue_containers(time.time()):
        if sb.remove_container(name):
            removed.append(name)
    with registry_lock:
        expired = [s for s in sessions.values() if s.box.name in removed or time.time() > s.deadline_epoch]
    for session in expired:
        with session.lock:
            if not session.ended:
                end_session(session)
    return removed


def reaper_loop() -> None:
    while True:
        with contextlib.suppress(Exception):
            reap_once()
        time.sleep(REAP_INTERVAL_S)
