"""Runs tasks one at a time from a bounded queue, so a queue of tickets can't exhaust the VM."""

from __future__ import annotations

import datetime as dt
import queue
import threading
import uuid

from langgraph.types import Command

from app.agent.graph import Deps, build_graph
from app.core.config import Settings
from app.db.store import AWAITING, DONE, Store
from app.evidence.manifest import build_manifest
from app.evidence.store import EvidenceStore
from app.llm.gateway import CallRecord, Gateway
from app.llm.pricing import call_cost, fetch_prices
from app.publish.github import Publisher, SourceAndPullRequests
from app.sandbox_client.client import SandboxClient


class QueueFull(RuntimeError):
    pass


class BudgetUsedUp(RuntimeError):
    pass


class Worker:
    def __init__(self, settings: Settings, store: Store, evidence: EvidenceStore) -> None:
        self.settings = settings
        self.store = store
        self.evidence = evidence
        self.sandbox = SandboxClient(settings.sandboxd_socket)
        self.publisher = Publisher(settings.github_token, settings.target_repo)
        self.source = SourceAndPullRequests(self.publisher, settings.target_commit)
        self.prices: dict[str, tuple[float, float]] = {}
        self.queue: queue.Queue[tuple[str, dict | None]] = queue.Queue(maxsize=settings.max_queue)
        threading.Thread(target=self._loop, daemon=True).start()

    def recover_unfinished(self) -> None:
        """A task waiting for approval survives a restart, durably. Anything cut off mid-run is
        reported as failed rather than left hanging, because its sandbox is already gone."""
        for task_id in self.store.unfinished_tasks():
            self._fail(task_id, "The server restarted during this task")

    def submit(self, ticket: str, client_ip: str) -> str:
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        if self.store.tokens_used_since(today) >= self.settings.daily_token_budget:
            raise BudgetUsedUp()
        task_id = uuid.uuid4().hex
        if self.queue.full():
            raise QueueFull()
        self.store.create_task(task_id, ticket, client_ip)
        self.store.add_event(task_id, "status_changed", "queued")
        try:
            self.queue.put_nowait((task_id, None))
        except queue.Full as error:
            self._fail(task_id, "The queue was full")
            raise QueueFull() from error
        return task_id

    def submit_drill(self, drill: str, client_ip: str) -> str:
        task_id = uuid.uuid4().hex
        if self.queue.full():
            raise QueueFull()
        self.store.create_task(task_id, f"Containment drill: {drill}", client_ip, kind="drill")
        self.store.add_event(task_id, "status_changed", "queued")
        try:
            self.queue.put_nowait((task_id, {"drill": drill}))
        except queue.Full as error:
            self._fail(task_id, "The queue was full")
            raise QueueFull() from error
        return task_id

    def _run_drill(self, task_id: str, drill: str) -> None:
        self.store.update_task(task_id, status="verifying")
        self.store.add_event(task_id, "status_changed", "verifying")
        task = self.store.get_task(task_id)
        result = self.sandbox.drill(f"{task_id}-{drill}", drill)
        contained = result.get("contained") is True
        outcome = "CONTAINED" if contained else "NOT_CONTAINED"
        rule = (
            "Every host-side check passed" if contained
            else "A host-side check failed: " + "; ".join(c["name"] for c in result.get("checks") or [] if not c["passed"])
        )
        self.store.add_event(task_id, "limit_hit", drill, {
            "checks": result.get("checks"), "termination": result.get("termination"), "seconds": result.get("seconds"),
            "attestation": result.get("attestation"), "exit_code": result.get("exit_code"),
        })
        self.evidence.put_blob(task_id, str(result.get("output_tail") or "").encode())
        self.store.update_task(task_id, outcome=outcome, outcome_rule=rule, status=DONE)
        self.store.add_event(task_id, "outcome", outcome, {"rule": rule})
        self.store.add_event(task_id, "status_changed", DONE)
        self.evidence.write_manifest(task_id, build_manifest(
            task_id=task_id, ticket=task["ticket"], repository=self.settings.target_repo, commit=self.settings.target_commit,
            script_sha256=None, replays=[result], events=self.store.events_after(task_id, -1), outcome=outcome, rule=rule, issue_url=None,
        ))

    def resume(self, task_id: str, decision: str, note: str | None) -> None:
        # A decision is never dropped for lack of queue room, because it blocks until there is some.
        self.store.add_event(task_id, "status_changed", "publishing")
        self.queue.put((task_id, {"decision": decision, "note": (note or "")[:1000] or None}))

    def _loop(self) -> None:
        while True:
            task_id, decision = self.queue.get()
            try:
                if decision and "drill" in decision:
                    self._run_drill(task_id, decision["drill"])
                else:
                    self._run(task_id, decision)
            except Exception as error:
                self._fail(task_id, f"{type(error).__name__}: {str(error)[:300]}")

    def _run(self, task_id: str, decision: dict | None) -> None:
        task = self.store.get_task(task_id)

        def on_call(record: CallRecord) -> None:
            if not self.prices:
                self.prices = fetch_prices()
            cost = call_cost(self.prices, record.model, record.prompt_tokens, record.completion_tokens)
            self.store.add_tokens(task_id, record.prompt_tokens + record.completion_tokens, cost)
            self.store.add_event(task_id, "llm_call", record.model, {**record.__dict__, "cost_usd": cost})

        gateway = Gateway(
            self.settings.inference_key, self.settings.primary_model, self.settings.fallback_model,
            self.settings.task_token_budget, on_call,
        )
        deps = Deps(self.settings, self.store, self.evidence, self.sandbox, self.publisher, gateway, self.source)
        graph = build_graph(deps, str(self.settings.data_dir / "checkpoints.sqlite3"))
        config = {"configurable": {"thread_id": task_id}, "recursion_limit": self.settings.max_explore_steps * 2 + 60}
        graph_input = Command(resume=decision) if decision else {"task_id": task_id, "ticket": task["ticket"]}
        try:
            graph.invoke(graph_input, config)
        except Exception as error:
            values = graph.get_state(config).values or {}
            if values.get("session_id"):
                self.sandbox.end_session(values["session_id"])
            # A late failure keeps the evidence gathered so far.
            self._fail(
                task_id, f"{type(error).__name__}: {str(error)[:300]}",
                script_sha256=values.get("script_sha256"),
                replays=[*(values.get("replays") or []), *(values.get("patched_replays") or []), *(values.get("regressions") or [])],
            )

    def _fail(self, task_id: str, reason: str, script_sha256: str | None = None, replays: list[dict] | None = None) -> None:
        task = self.store.get_task(task_id)
        if task is None or task["status"] in (DONE, AWAITING):
            return
        self.store.update_task(task_id, outcome="FAILED", outcome_rule=reason, status=DONE)
        self.store.add_event(task_id, "error", reason)
        self.store.add_event(task_id, "outcome", "FAILED", {"rule": reason})
        self.store.add_event(task_id, "status_changed", DONE)
        self.evidence.write_manifest(task_id, build_manifest(
            task_id=task_id, ticket=task["ticket"], repository=self.settings.target_repo,
            commit=self.settings.target_commit, script_sha256=script_sha256, replays=replays or [],
            events=self.store.events_after(task_id, -1), outcome="FAILED", rule=reason, issue_url=task["issue_url"],
        ))
