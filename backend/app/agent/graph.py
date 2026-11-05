"""The agent graph: reproduce, file the bug, propose and verify a patch, wait for a developer.

Code decides every outcome. Nodes can run again after a restart, so side effects carry
idempotency keys. The wait for approval is a durable interrupt.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import re
import sqlite3
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent import actions as act
from app.agent import outcome as rules
from app.agent import policy, prompts
from app.agent.freeze import freeze, render_check, simplify_trail
from app.core.config import Settings
from app.db.store import AWAITING, Store
from app.evidence.manifest import build_manifest
from app.evidence.store import EvidenceStore, sha256_hex
from app.llm.gateway import BudgetExceeded, Gateway, LLMError
from app.publish.github import Publisher, SourceAndPullRequests, issue_body, pull_request_body
from app.sandbox_client.client import SandboxClient, SandboxUnavailable, SessionEnded
from app.tickets.intake import find_injection

MAX_READ_ROUNDS = 2
MAX_READ_CHARS = 30_000
MAX_SOURCES_IN_PROMPT = 8


class State(TypedDict, total=False):
    task_id: str
    ticket: str
    session_id: str | None
    page: dict | None
    screenshot_sha: str | None
    trail: list[dict]
    notes: list[str]
    steps_used: int
    finish: dict | None
    script_sha256: str | None
    replays: list[dict]
    outcome: str | None
    rule: str | None
    issue_url: str | None
    failure: str | None
    attempt: int
    feedback: list[str]
    edits: list[dict] | None
    patch_sha256: str | None
    patch_record: dict | None
    explanation: str | None
    patched_replays: list[dict]
    regressions: list[dict]
    fix_verified: bool
    decision: dict | None
    pull_request_url: str | None
    decided_at: str | None
    flagged: list[str]


@dataclasses.dataclass
class Deps:
    settings: Settings
    store: Store
    evidence: EvidenceStore
    sandbox: SandboxClient
    publisher: Publisher
    gateway: Gateway
    source: SourceAndPullRequests


def describe(step: dict) -> str:
    parts = [step["action"], *(str(step[k]) for k in ("path", "selector", "value") if step.get(k))]
    return " ".join(parts)[:300]


def strip_page(page: dict) -> dict:
    """Screenshots live in the evidence store, not in checkpoints."""
    return {key: value for key, value in page.items() if key != "screenshot_b64"}


class Nodes:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    def _event(self, state: State, event_type: str, text: str | None = None, data: dict | None = None) -> None:
        self.deps.store.add_event(state["task_id"], event_type, text, data)

    def _status(self, state: State, status: str) -> None:
        self.deps.store.update_task(state["task_id"], status=status)
        self._event(state, "status_changed", status)

    def _keep_screenshot(self, state: State, page: dict) -> str | None:
        encoded = page.get("screenshot_b64")
        if not encoded:
            return None
        return self.deps.evidence.put_blob(state["task_id"], base64.b64decode(encoded))

    def _store_run(self, state: State, run: dict) -> dict:
        """Move hostile output into opaque evidence, so only hashes stay in the state."""
        for artifact in run.get("artifacts") or []:
            self.deps.evidence.put_blob(state["task_id"], base64.b64decode(artifact.pop("content_b64")))
        # The script prints this. It is checked against the sandbox's own address, and capped.
        match = re.search(r"^FINAL URL: (http://127\.0\.0\.1:9090/[^\s]{0,400})$", run.get("stdout") or "", re.MULTILINE)
        run["final_url"] = match.group(1) if match else None
        for stream in ("stdout", "stderr"):
            text = run.pop(stream, "")
            run[f"{stream}_blob"] = self.deps.evidence.put_blob(state["task_id"], text.encode())
        return run

    def _manifest(self, state: State, outcome: str, rule: str) -> None:
        self.deps.evidence.write_manifest(state["task_id"], build_manifest(
            task_id=state["task_id"], ticket=state["ticket"], repository=self.deps.settings.target_repo,
            commit=self.deps.settings.target_commit, script_sha256=state.get("script_sha256"),
            replays=[*(state.get("replays") or []), *(state.get("patched_replays") or []), *(state.get("regressions") or [])],
            events=self.deps.store.events_after(state["task_id"], -1), outcome=outcome, rule=rule,
            issue_url=state.get("issue_url"), patch=state.get("patch_record"),
            pull_request_url=state.get("pull_request_url"), decided_at=state.get("decided_at"),
        ))

    def _flag_hostile(self, state: State, source: str, text: str, flagged: list[str]) -> list[str]:
        """Show a developer the attempt. This never changes what the agent does or the outcome."""
        if source in flagged:
            return flagged
        excerpt = find_injection(text)
        if excerpt is None:
            return flagged
        self._event(state, "injection_flagged", excerpt, {"source": source})
        return [*flagged, source]

    def intake(self, state: State) -> State:
        self._status(state, "exploring")
        flagged = self._flag_hostile(state, "ticket", state["ticket"], [])
        try:
            session = self.deps.sandbox.start_session(f"task-{state['task_id']}")
        except SandboxUnavailable as error:
            return {"failure": f"Sandbox unavailable: {error}", "session_id": None, "flagged": flagged}
        self._event(state, "sandbox_created", "Browser session started", {
            "session_id": session["session_id"], "attestation": session["attestation"],
            "deadline_epoch": session["deadline_epoch"],
        })
        page = session.get("initial") or {}
        shot = self._keep_screenshot(state, page)
        self._event(state, "browser_action", "Logged in with a throwaway test user", {"url": page.get("url"), "screenshot_sha256": shot})
        flagged = self._flag_hostile(state, "page", str(page.get("text") or ""), flagged)
        return {
            "session_id": session["session_id"], "page": strip_page(page), "screenshot_sha": shot, "trail": [],
            "notes": [], "steps_used": 0, "finish": None, "failure": None, "replays": [], "flagged": flagged,
        }

    def explore(self, state: State) -> State:
        limit = self.deps.settings.max_explore_steps
        used = state.get("steps_used", 0)
        screenshot = None
        if state.get("screenshot_sha"):
            screenshot = self.deps.evidence.get_blob(state["task_id"], state["screenshot_sha"])
        messages = prompts.explore_messages(state["ticket"], state["notes"], state["page"] or {}, screenshot, limit - used)
        try:
            call = self.deps.gateway.call_tool(messages, act.NEXT_ACTION_TOOL)
        except (LLMError, BudgetExceeded) as error:
            return {"failure": f"Model unavailable: {error}"}

        action, problem = act.parse_next_action(call.arguments)
        notes = list(state["notes"])
        if action is None:
            notes.append(f"Your last reply was invalid: {problem}")
            self._event(state, "error", "The model returned an invalid action, which was refused")
            return {"notes": notes, "steps_used": used + 1}

        if action.reason:
            self._event(state, "agent_thought", action.reason)
        if action.action == "finish":
            return {"finish": action.model_dump(), "steps_used": used + 1}

        step = action.for_sandbox()
        try:
            result = self.deps.sandbox.act(state["session_id"], step)
        except SessionEnded as error:
            return {"failure": f"Sandbox ended during exploration: {error}"}
        except SandboxUnavailable as error:
            return {"failure": f"Sandbox unavailable: {error}"}

        trail = list(state["trail"])
        if result.get("ok") and action.action in act.REPLAYABLE:
            trail.append(action.model_dump())
        notes.append(f"{describe(step)} -> {'ok' if result.get('ok') else 'failed: ' + str(result.get('error'))[:200]}")
        shot = self._keep_screenshot(state, result) or state.get("screenshot_sha")
        self._event(state, "browser_action", describe(step), {
            "ok": bool(result.get("ok")), "url": result.get("url"), "screenshot_sha256": shot,
        })
        update: State = {"trail": trail, "notes": notes, "steps_used": used + 1, "screenshot_sha": shot}
        if not result.get("refused"):
            update["page"] = strip_page(result)
            update["flagged"] = self._flag_hostile(state, "page", str(result.get("text") or ""), list(state.get("flagged") or []))
        return update

    def route_explore(self, state: State) -> str:
        if state.get("failure") or state.get("finish"):
            return "end_session"
        if state.get("steps_used", 0) >= self.deps.settings.max_explore_steps:
            return "end_session"
        return "explore"

    def end_session(self, state: State) -> State:
        """The session is destroyed on every path, before anything is decided."""
        session_id = state.get("session_id")
        if not session_id:
            return {}
        try:
            destruction = self.deps.sandbox.end_session(session_id)
        except Exception as error:
            return {"session_id": None, "failure": f"Couldn't confirm session destruction: {type(error).__name__}"}
        self._event(state, "sandbox_destroyed", "Browser session destroyed", {
            "destroyed": destruction.get("destroyed"), "confirmed_at": destruction.get("confirmed_at"),
        })
        update: State = {"session_id": None}
        if destruction.get("destroyed") is not True:
            update["failure"] = "Session container destruction wasn't confirmed"
        return update

    def route_after_session(self, state: State) -> str:
        if state.get("failure"):
            return "finalize"
        finish = state.get("finish")
        if not finish or not finish.get("believes_reproduced") or not state.get("trail"):
            return "report_not_reproduced"
        return "freeze_repro"

    def report_not_reproduced(self, state: State) -> State:
        if state.get("finish"):
            rule = "The agent finished without observing the bug, so nothing was replayed"
        else:
            rule = f"The agent used its {self.deps.settings.max_explore_steps} actions without observing the bug"
        return {"outcome": "NOT_REPRODUCED", "rule": rule}

    def freeze_repro(self, state: State) -> State:
        self._status(state, "confirming")
        trail = simplify_trail([act.NextAction.model_validate(step) for step in state["trail"]])
        expectation = act.Expectation.model_validate(state["finish"]["expectation"])
        script, digest = freeze(trail, expectation)
        self.deps.evidence.put_blob(state["task_id"], script.encode())
        self._event(state, "repro_frozen", f"Script frozen with {len(trail)} steps", {"script_sha256": digest})
        return {"script_sha256": digest, "trail": [step.model_dump() for step in trail]}

    def _frozen_script(self, state: State) -> str | None:
        script = self.deps.evidence.get_blob(state["task_id"], state["script_sha256"])
        if script is None or sha256_hex(script) != state["script_sha256"]:
            return None
        return script.decode()

    def confirm_repro(self, state: State) -> State:
        script = self._frozen_script(state)
        if script is None:
            return {"failure": "Frozen script doesn't match its hash"}
        replays = []
        required = self.deps.settings.replay_count
        for number in range(1, required + 1):
            run_key = f"{state['task_id']}-baseline-{number}"
            try:
                replay = self._store_run(state, self.deps.sandbox.replay(run_key, script, state["script_sha256"]))
            except SandboxUnavailable as error:
                return {"failure": f"Sandbox unavailable: {error}", "replays": replays}
            replays.append(replay)
            self._replay_event(state, "baseline", number, replay)
            if replay.get("symptom_observed") is not True:
                break  # One run without the symptom already rules out REPRODUCED.
        decision = rules.decide_reproduction(replays, state["script_sha256"], required)
        if len(replays) < required and replays[-1].get("termination") == "exited":
            decision = rules.Decision("NOT_REPRODUCED", f"Replay {len(replays)} exited {replays[-1].get('exit_code')}, not the symptom code")
        return {"replays": replays, "outcome": decision.outcome, "rule": decision.rule}

    def _replay_event(self, state: State, stage: str, number: int, replay: dict) -> None:
        shot = next((a["sha256"] for a in replay.get("artifacts") or [] if a["name"] == "final.png"), None)
        self._event(state, "replay_finished", f"Replay {number}: exit {replay.get('exit_code')}, {replay.get('termination')}", {
            "stage": stage, "number": number, "exit_code": replay.get("exit_code"),
            "termination": replay.get("termination"), "symptom_observed": replay.get("symptom_observed"),
            "destroyed": (replay.get("destruction") or {}).get("destroyed"), "seconds": replay.get("seconds"),
            "screenshot_sha256": shot, "final_url": replay.get("final_url"),
        })

    def route_after_confirm(self, state: State) -> str:
        if state.get("failure"):
            return "finalize"
        return "file_issue" if state.get("outcome") == "REPRODUCED" else "finalize"

    def file_issue(self, state: State) -> State:
        self._status(state, "filing")
        finish = state.get("finish") or {}
        symptom = finish.get("symptom") or "The expected behavior didn't happen."
        steps = [describe(step) for step in state["trail"]]
        title, summary = "Reproduced customer bug", symptom
        try:
            call = self.deps.gateway.call_tool(prompts.issue_messages(state["ticket"], symptom, steps), act.WRITE_ISSUE_TOOL)
            title = str(call.arguments.get("title") or title)[:200]
            summary = str(call.arguments.get("summary") or summary)[:2000]
        except (LLMError, BudgetExceeded) as error:
            # The evidence stands without the model's wording, so filing carries on.
            self._event(state, "error", f"Model summary unavailable, using the observed symptom: {type(error).__name__}")
        script = self._frozen_script(state)
        if script is None:
            return {"failure": "Frozen script doesn't match its hash"}
        screenshot_url = None
        for artifact in state["replays"][-1].get("artifacts") or []:
            if artifact["name"] == "final.png":
                png = self.deps.evidence.get_blob(state["task_id"], artifact["sha256"])
                if png:
                    screenshot_url = self.deps.publisher.upload_screenshot(state["task_id"], "failing-state.png", png)
        body = issue_body(
            ticket=state["ticket"], summary=summary, script=script, script_sha256=state["script_sha256"],
            replays=state["replays"], rule=state["rule"], repository=self.deps.settings.target_repo,
            commit=self.deps.settings.target_commit, screenshot_url=screenshot_url,
            manifest_note=f"Task ID: `{state['task_id']}`. The evidence manifest is available from the Show Me server.",
        )
        url = self.deps.publisher.file_issue(state["task_id"], title, body)
        self.deps.store.update_task(state["task_id"], issue_url=url, explanation=summary)
        self._event(state, "issue_filed", url, {"issue_url": url})
        return {"issue_url": url, "attempt": 0, "feedback": [], "patched_replays": [], "regressions": [], "fix_verified": False}

    def route_after_issue(self, state: State) -> str:
        if state.get("failure") or not self.deps.settings.fix_phase_enabled:
            return "finalize"
        return "propose_patch"

    def propose_patch(self, state: State) -> State:
        self._status(state, "patching")
        attempt = state.get("attempt", 0) + 1
        feedback = list(state.get("feedback") or [])
        files = [path for path in self.deps.source.list_files() if policy.path_allowed(path)]
        allowed = set(files)
        sources: dict[str, str] = {}
        finish = state.get("finish") or {}
        steps = [describe(step) for step in state["trail"]]
        check = render_check(act.Expectation.model_validate(finish["expectation"]))
        for round_number in range(MAX_READ_ROUNDS + 2):
            must_propose = round_number >= MAX_READ_ROUNDS
            messages = prompts.patch_messages(
                state["ticket"], finish.get("symptom") or "", steps, check, files, sources, feedback, must_propose,
            )
            try:
                call = self.deps.gateway.call_tool(messages, act.PROPOSE_ONLY_TOOL if must_propose else act.PATCH_STEP_TOOL)
            except (LLMError, BudgetExceeded) as error:
                return {"attempt": attempt, "edits": None, "feedback": [*feedback, f"Model unavailable: {type(error).__name__}"]}
            arguments = call.arguments
            if arguments.get("action") == "propose":
                try:
                    edits = policy.parse_edits(arguments.get("edits"))
                except ValueError as error:
                    feedback.append(f"Invalid proposal: {error}")
                    continue
                explanation = str(arguments.get("explanation") or "")[:2000]
                self._event(state, "patch_proposed", f"Attempt {attempt}", {"attempt": attempt, "files": sorted({e.path for e in edits})})
                return {
                    "attempt": attempt, "feedback": feedback, "explanation": explanation,
                    "edits": [dataclasses.asdict(edit) for edit in edits],
                }
            paths = arguments.get("paths")
            wanted = [p for p in paths if isinstance(p, str) and p in allowed][:4] if isinstance(paths, list) else []
            if not wanted:
                feedback.append("Your read request named no listed path")
                continue
            for path in wanted:
                if len(sources) < MAX_SOURCES_IN_PROMPT:
                    sources[path] = self.deps.source.read_file(path)[:MAX_READ_CHARS]
            self._event(state, "agent_thought", "Reading source: " + ", ".join(wanted))
        return {"attempt": attempt, "edits": None, "feedback": [*feedback, "No patch was proposed in time"]}

    def verify_patch(self, state: State) -> State:
        self._status(state, "verifying")
        attempt = state["attempt"]
        feedback = list(state.get("feedback") or [])
        if not state.get("edits"):
            return {"fix_verified": False}
        edits = [policy.Edit(**edit) for edit in state["edits"]]
        patch = policy.build_patch(edits, self.deps.source.read_file)
        record = {"sha256": patch.sha256, "policy_passed": patch.passed, "policy_reasons": patch.reasons}
        self._event(state, "policy_checked", "Passed" if patch.passed else "; ".join(patch.reasons)[:1000], {
            "attempt": attempt, "passed": patch.passed, "changed_lines": patch.changed_lines,
        })
        if not patch.passed:
            return {"fix_verified": False, "patch_record": record, "feedback": [*feedback, f"Attempt {attempt} broke policy: {'; '.join(patch.reasons)[:500]}"]}
        self.deps.evidence.put_blob(state["task_id"], patch.diff.encode())
        script = self._frozen_script(state)
        if script is None:
            return {"failure": "Frozen script doesn't match its hash"}

        replays, regressions = [], list(state.get("regressions") or [])
        required = self.deps.settings.replay_count
        all_patched = list(state.get("patched_replays") or [])
        try:
            for number in range(1, required + 1):
                run_key = f"{state['task_id']}-fix-{patch.sha256[:16]}-{number}"
                replay = self._store_run(state, self.deps.sandbox.replay(run_key, script, state["script_sha256"], patch.diff))
                replays.append(replay)
                self._replay_event(state, "patched", number, replay)
                if replay.get("symptom_observed") is not False or replay.get("exit_code") != 0:
                    break
            regression_patched = regression_baseline = None
            if len(replays) == required and replays[-1].get("exit_code") == 0:
                self._event(state, "agent_thought", "Running the regression tests with and without the patch")
                regression_baseline = self._store_run(state, self.deps.sandbox.regression(f"regression-v3-{self.deps.settings.target_commit}"))
                regression_patched = self._store_run(state, self.deps.sandbox.regression(f"{state['task_id']}-regression-v3-{patch.sha256[:16]}", patch.diff))
                regressions = [*regressions, regression_baseline, regression_patched]
        except SandboxUnavailable as error:
            return {"failure": f"Sandbox unavailable: {error}"}

        decision = rules.decide_fix(
            replays=replays, script_sha256=state["script_sha256"], patch_sha256=patch.sha256, required=required,
            regression_patched=regression_patched, regression_baseline=regression_baseline,
        )
        update: State = {
            "patched_replays": [*all_patched, *replays], "regressions": regressions, "patch_record": record, "patch_sha256": patch.sha256,
        }
        self._event(state, "patch_verified", decision.rule, {"attempt": attempt, "outcome": decision.outcome})
        if decision.outcome == "FIX_VERIFIED":
            self.deps.store.update_task(state["task_id"], diff=patch.diff)
            return {**update, "fix_verified": True, "outcome": "FIX_VERIFIED", "rule": decision.rule}
        if decision.outcome == "FAILED":
            return {**update, "failure": decision.rule}
        tail = next((r.get("error") for r in reversed(replays) if r.get("error")), None)
        note = f"Attempt {attempt} was rejected: {decision.rule}" + (f" ({tail[:300]})" if tail else "")
        return {**update, "fix_verified": False, "feedback": [*feedback, note]}

    def route_after_verify(self, state: State) -> str:
        if state.get("failure"):
            return "finalize"
        if state.get("fix_verified"):
            return "await_approval"
        if state.get("attempt", 0) < self.deps.settings.max_patch_attempts:
            return "propose_patch"
        return "report_no_fix"

    def report_no_fix(self, state: State) -> State:
        reasons = state.get("feedback") or []
        rule = f"No patch was verified in {state.get('attempt')} attempts. The filed issue stands"
        if reasons:
            rule += f". Last reason: {reasons[-1][:300]}"
        return {"outcome": "NO_VERIFIED_FIX", "rule": rule}

    def await_approval(self, state: State) -> State:
        """Waits as long as it takes. This node runs again from the top when it resumes."""
        task = self.deps.store.get_task(state["task_id"])
        if task["status"] != "publishing":
            self.deps.store.update_task(state["task_id"], outcome="FIX_VERIFIED", outcome_rule=state["rule"], explanation=state.get("explanation"))
            self._manifest(state, "FIX_VERIFIED", state["rule"])
            self._status(state, AWAITING)
            self._event(state, "awaiting_approval", "Waiting for a developer to approve or reject")
        decision = interrupt({"task_id": state["task_id"]})
        at = dt.datetime.now(dt.timezone.utc).isoformat()
        return {"decision": decision, "decided_at": at}

    def route_after_decision(self, state: State) -> str:
        return "open_pr" if (state.get("decision") or {}).get("decision") == "approve" else "record_rejection"

    def record_rejection(self, state: State) -> State:
        self._event(state, "decision_recorded", "rejected", {"note": (state.get("decision") or {}).get("note")})
        return {"outcome": "REJECTED", "rule": "A developer rejected the patch, so no pull request was opened"}

    def open_pr(self, state: State) -> State:
        self._event(state, "decision_recorded", "approved", {"note": (state.get("decision") or {}).get("note")})
        edits = [policy.Edit(**edit) for edit in state["edits"]]
        patch = policy.build_patch(edits, self.deps.source.read_file)
        if not patch.passed or patch.sha256 != state["patch_sha256"]:
            return {"failure": "The approved patch no longer matches what was verified"}
        url = self.deps.source.open_pull_request(
            state["task_id"], f"Fix reproduced bug, Show Me task {state['task_id'][:8]}",
            pull_request_body(
                issue_url=state.get("issue_url"), explanation=state.get("explanation") or "", rule=state["rule"],
                script_sha256=state["script_sha256"], patch_sha256=patch.sha256,
            ),
            patch.files,
        )
        self.deps.store.update_task(state["task_id"], pull_request_url=url)
        self._event(state, "pull_request_opened", url, {"pull_request_url": url})
        return {"pull_request_url": url, "outcome": "APPROVED", "rule": f"{state['rule']}. A developer approved it"}

    def finalize(self, state: State) -> State:
        """Record the outcome and seal the evidence. A failure is reported as a failure."""
        outcome, rule = state.get("outcome"), state.get("rule")
        if state.get("failure"):
            outcome, rule = "FAILED", state["failure"]
        outcome = outcome or "FAILED"
        rule = rule or "No outcome was recorded"
        self.deps.store.update_task(state["task_id"], outcome=outcome, outcome_rule=rule)
        self._event(state, "outcome", outcome, {"rule": rule})
        self._manifest(state, outcome, rule)
        self.deps.store.update_task(state["task_id"], status="done")
        self._event(state, "status_changed", "done")
        return {"outcome": outcome, "rule": rule}


def build_graph(deps: Deps, checkpoint_path: str):
    nodes = Nodes(deps)
    graph = StateGraph(State)
    for name in (
        "intake", "explore", "end_session", "report_not_reproduced", "freeze_repro", "confirm_repro", "file_issue",
        "propose_patch", "verify_patch", "report_no_fix", "await_approval", "record_rejection", "open_pr", "finalize",
    ):
        graph.add_node(name, getattr(nodes, name))

    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: "finalize" if s.get("failure") else "explore", ["finalize", "explore"])
    graph.add_conditional_edges("explore", nodes.route_explore, ["explore", "end_session"])
    graph.add_conditional_edges("end_session", nodes.route_after_session, ["finalize", "report_not_reproduced", "freeze_repro"])
    graph.add_edge("report_not_reproduced", "finalize")
    graph.add_edge("freeze_repro", "confirm_repro")
    graph.add_conditional_edges("confirm_repro", nodes.route_after_confirm, ["file_issue", "finalize"])
    graph.add_conditional_edges("file_issue", nodes.route_after_issue, ["propose_patch", "finalize"])
    graph.add_edge("propose_patch", "verify_patch")
    graph.add_conditional_edges("verify_patch", nodes.route_after_verify, ["await_approval", "propose_patch", "report_no_fix", "finalize"])
    graph.add_edge("report_no_fix", "finalize")
    graph.add_conditional_edges("await_approval", nodes.route_after_decision, ["open_pr", "record_rejection"])
    graph.add_edge("record_rejection", "finalize")
    graph.add_edge("open_pr", "finalize")
    graph.add_edge("finalize", END)
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    return graph.compile(checkpointer=SqliteSaver(connection))
