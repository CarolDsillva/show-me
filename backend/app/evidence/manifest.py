"""Builds the evidence manifest from host-side records. See contracts/evidence.schema.json."""

from __future__ import annotations

from app.core.config import INFERENCE_BASE_URL
from app.evidence.store import sha256_hex


def _session_record(events: list[dict]) -> list[dict]:
    created = next((e for e in events if e["type"] == "sandbox_created"), None)
    destroyed = next((e for e in events if e["type"] == "sandbox_destroyed"), None)
    if not created:
        return []
    info = created.get("data") or {}
    attestation = info.get("attestation") or {}
    done = (destroyed or {}).get("data") or {}
    return [{
        "id": info.get("session_id", "unknown"),
        "kind": "session",
        "image_id": attestation.get("image_id") or "unknown",
        "attestation": attestation,
        "started_at": created["at"],
        "termination": "exited" if destroyed else "sandbox_error",
        "destroyed": done.get("destroyed") is True,
        "destroyed_confirmed_at": done.get("confirmed_at") or created["at"],
    }]


def _replay_record(replay: dict) -> dict:
    attestation = replay.get("attestation") or {}
    destruction = replay.get("destruction") or {}
    record = {
        "id": replay.get("sandbox_id") or replay["run_key"],
        "kind": "replay",
        "image_id": attestation.get("image_id") or "unknown",
        "attestation": attestation,
        "exit_code": replay.get("exit_code"),
        "termination": replay.get("termination", "sandbox_error"),
        "symptom_observed": replay.get("symptom_observed"),
        "script_sha256": replay.get("script_sha256"),
        "artifacts": [
            {"name": a["name"], "sha256": a["sha256"], "bytes": a["bytes"]} for a in replay.get("artifacts") or []
        ],
        "destroyed": destruction.get("destroyed") is True,
        "destroyed_confirmed_at": destruction.get("confirmed_at") or replay.get("finished_at"),
    }
    for field in ("started_at", "finished_at", "stdout_sha256", "stderr_sha256"):
        if replay.get(field):
            record[field] = replay[field]
    return record


def build_manifest(
    *, task_id: str, ticket: str, repository: str, commit: str, script_sha256: str | None,
    replays: list[dict], events: list[dict], outcome: str, rule: str, issue_url: str | None,
) -> dict:
    llm_calls = []
    for event in events:
        if event["type"] == "llm_call":
            data = event["data"]
            llm_calls.append({
                "base_url": INFERENCE_BASE_URL,
                "model": data["model"],
                "prompt_sha256": data["prompt_sha256"],
                "response_sha256": data["response_sha256"],
                "prompt_tokens": data["prompt_tokens"],
                "completion_tokens": data["completion_tokens"],
                "at": data["at"],
            })
    return {
        "schema_version": "0.1.0",
        "task_id": task_id,
        "ticket_sha256": sha256_hex(ticket.encode()),
        "target": {"repository": repository, "commit": commit},
        "frozen_script_sha256": script_sha256,
        "patch": None,
        "sandboxes": [*_session_record(events), *(_replay_record(r) for r in replays)],
        "llm_calls": llm_calls,
        "publishing": {"issue_url": issue_url, "pull_request_url": None, "decided_at": None},
        "outcome": {"value": outcome, "rule": rule},
    }
