"""Outcome rules. Plain code, using only host-side results. The model can't change them."""

from __future__ import annotations

import dataclasses

SYMPTOM_EXIT = 42


@dataclasses.dataclass(frozen=True)
class Decision:
    outcome: str
    rule: str


def decide_reproduction(replays: list[dict], script_sha256: str, required: int) -> Decision:
    """REPRODUCED needs every replay to match the frozen hash, run attested and destroyed,
    and end on the symptom exit code. A broken step counts as not reproduced."""
    if len(replays) != required:
        return Decision("FAILED", f"Expected {required} replays, got {len(replays)}")
    for index, replay in enumerate(replays, start=1):
        destruction = replay.get("destruction") or {}
        if destruction.get("destroyed") is not True:
            return Decision("FAILED", f"Replay {index}: container destruction wasn't confirmed")
        attestation = replay.get("attestation") or {}
        if not attestation or attestation.get("mismatches"):
            return Decision("FAILED", f"Replay {index}: attestation failed or is missing")
        if replay.get("termination") == "sandbox_error":
            return Decision("FAILED", f"Replay {index}: the sandbox failed")
    for index, replay in enumerate(replays, start=1):
        if replay.get("script_sha256") != script_sha256:
            return Decision("FAILED", f"Replay {index}: script hash doesn't match the frozen script")
        if replay.get("termination") != "exited":
            return Decision("NOT_REPRODUCED", f"Replay {index} ended as {replay.get('termination')}")
        if replay.get("exit_code") != SYMPTOM_EXIT or replay.get("symptom_observed") is not True:
            return Decision("NOT_REPRODUCED", f"Replay {index} exited {replay.get('exit_code')}, not the symptom code")
    return Decision("REPRODUCED", f"{required} of {required} fresh attested replays of the frozen script showed the symptom")
