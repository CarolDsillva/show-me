"""The agent graph, up to filing the issue. The fix phase is added on Day 3."""

from __future__ import annotations

import base64
import dataclasses
import sqlite3
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.agent import actions as act
from app.agent import outcome as rules
from app.agent import prompts
from app.agent.freeze import freeze, simplify_trail
from app.core.config import Settings
from app.db.store import Store
from app.evidence.manifest import build_manifest
from app.evidence.store import EvidenceStore, sha256_hex
from app.llm.gateway import BudgetExceeded, Gateway, LLMError
from app.publish.github import Publisher, issue_body
from app.sandbox_client.client import SandboxClient, SandboxUnavailable, SessionEnded


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


@dataclasses.dataclass
class Deps:
    settings: Settings
    store: Store
    evidence: EvidenceStore
    sandbox: SandboxClient
    publisher: Publisher
    gateway: Gateway


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

    def intake(self, state: State) -> State:
        self._status(state, "exploring")
        try:
            session = self.deps.sandbox.start_session(f"task-{state['task_id']}")
        except SandboxUnavailable as error:
            return {"failure": f"Sandbox unavailable: {error}", "session_id": None}
        self._event(state, "sandbox_created", "Browser session started", {
            "session_id": session["session_id"], "attestation": session["attestation"],
            "deadline_epoch": session["deadline_epoch"],
        })
        page = session.get("initial") or {}
        shot = self._keep_screenshot(state, page)
        self._event(state, "browser_action", "Logged in with a throwaway test user", {"url": page.get("url"), "screenshot_sha256": shot})
        return {
            "session_id": session["session_id"], "page": strip_page(page), "screenshot_sha": shot, "trail": [],
            "notes": [], "steps_used": 0, "finish": None, "failure": None, "replays": [],
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
        return update

    def route_explore(self, state: State) -> str:
        if state.get("failure"):
            return "end_session"
        if state.get("finish"):
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
        if not finish or not finish.get("believes_reproduced"):
            return "report_not_reproduced"
        if not state.get("trail"):
            return "report_not_reproduced"
        return "freeze_repro"

    def report_not_reproduced(self, state: State) -> State:
        finish = state.get("finish") or {}
        if finish:
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
        return {"script_sha256": digest}

    def confirm_repro(self, state: State) -> State:
        script = self.deps.evidence.get_blob(state["task_id"], state["script_sha256"])
        if script is None or sha256_hex(script) != state["script_sha256"]:
            return {"failure": "Frozen script doesn't match its hash"}
        replays = []
        for number in range(1, self.deps.settings.replay_count + 1):
            run_key = f"{state['task_id']}-baseline-{number}"
            try:
                replay = self.deps.sandbox.replay(run_key, script.decode(), state["script_sha256"])
            except SandboxUnavailable as error:
                return {"failure": f"Sandbox unavailable: {error}", "replays": replays}
            for artifact in replay.get("artifacts") or []:
                self.deps.evidence.put_blob(state["task_id"], base64.b64decode(artifact.pop("content_b64")))
            for stream in ("stdout", "stderr"):
                text = replay.pop(stream, "")
                replay[f"{stream}_blob"] = self.deps.evidence.put_blob(state["task_id"], text.encode())
            replays.append(replay)
            self._event(state, "replay_finished", f"Replay {number}: exit {replay.get('exit_code')}, {replay.get('termination')}", {
                "exit_code": replay.get("exit_code"), "termination": replay.get("termination"),
                "symptom_observed": replay.get("symptom_observed"), "destroyed": (replay.get("destruction") or {}).get("destroyed"),
                "seconds": replay.get("seconds"),
            })
            if replay.get("symptom_observed") is not True:
                # One run without the symptom already rules out REPRODUCED.
                break
        decision = rules.decide_reproduction(replays, state["script_sha256"], self.deps.settings.replay_count)
        if len(replays) < self.deps.settings.replay_count and replays and replays[-1].get("termination") == "exited":
            decision = rules.Decision("NOT_REPRODUCED", f"Replay {len(replays)} exited {replays[-1].get('exit_code')}, not the symptom code")
        return {"replays": replays, "outcome": decision.outcome, "rule": decision.rule}

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
        script = self.deps.evidence.get_blob(state["task_id"], state["script_sha256"]).decode()
        screenshot_url = None
        last = state["replays"][-1]
        for artifact in last.get("artifacts") or []:
            if artifact["name"] == "final.png":
                png = self.deps.evidence.get_blob(state["task_id"], artifact["sha256"])
                if png:
                    screenshot_url = self.deps.publisher.upload_screenshot(state["task_id"], "failing-state.png", png)
        body = issue_body(
            ticket=state["ticket"], summary=summary, script=script, script_sha256=state["script_sha256"],
            replays=state["replays"], rule=state["rule"], repository=self.deps.settings.target_repo,
            commit=self.deps.settings.target_commit, screenshot_url=screenshot_url,
            manifest_note=f"Task ID: `{state['task_id']}`. The evidence manifest is available from the ReproFix server.",
        )
        url = self.deps.publisher.file_issue(state["task_id"], title, body)
        self.deps.store.update_task(state["task_id"], issue_url=url, explanation=summary)
        self._event(state, "issue_filed", url, {"issue_url": url})
        return {"issue_url": url}

    def finalize(self, state: State) -> State:
        """Record the outcome and seal the evidence. A failure is reported as a failure."""
        outcome, rule = state.get("outcome"), state.get("rule")
        if state.get("failure"):
            outcome, rule = "FAILED", state["failure"]
        outcome = outcome or "FAILED"
        rule = rule or "No outcome was recorded"
        self.deps.store.update_task(state["task_id"], outcome=outcome, outcome_rule=rule)
        self.deps.evidence.write_manifest(state["task_id"], build_manifest(
            task_id=state["task_id"], ticket=state["ticket"], repository=self.deps.settings.target_repo,
            commit=self.deps.settings.target_commit, script_sha256=state.get("script_sha256"),
            replays=state.get("replays") or [], events=self.deps.store.events_after(state["task_id"], -1),
            outcome=outcome, rule=rule, issue_url=state.get("issue_url"),
        ))
        self._event(state, "outcome", outcome, {"rule": rule})
        self.deps.store.update_task(state["task_id"], status="done")
        self._event(state, "status_changed", "done")
        return {"outcome": outcome, "rule": rule}


def build_graph(deps: Deps, checkpoint_path: str):
    nodes = Nodes(deps)
    graph = StateGraph(State)
    graph.add_node("intake", nodes.intake)
    graph.add_node("explore", nodes.explore)
    graph.add_node("end_session", nodes.end_session)
    graph.add_node("report_not_reproduced", nodes.report_not_reproduced)
    graph.add_node("freeze_repro", nodes.freeze_repro)
    graph.add_node("confirm_repro", nodes.confirm_repro)
    graph.add_node("file_issue", nodes.file_issue)
    graph.add_node("finalize", nodes.finalize)

    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: "finalize" if s.get("failure") else "explore", ["finalize", "explore"])
    graph.add_conditional_edges("explore", nodes.route_explore, ["explore", "end_session"])
    graph.add_conditional_edges("end_session", nodes.route_after_session, ["finalize", "report_not_reproduced", "freeze_repro"])
    graph.add_edge("report_not_reproduced", "finalize")
    graph.add_edge("freeze_repro", "confirm_repro")
    graph.add_conditional_edges("confirm_repro", nodes.route_after_confirm, ["file_issue", "finalize"])
    graph.add_edge("file_issue", "finalize")
    graph.add_edge("finalize", END)
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    return graph.compile(checkpointer=SqliteSaver(connection))
