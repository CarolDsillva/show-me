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


def decide_fix(
    *, replays: list[dict], script_sha256: str, patch_sha256: str, required: int,
    regression_patched: dict | None, regression_baseline: dict | None,
) -> Decision:
    """FIX_VERIFIED needs the frozen script to pass on the patched code in every fresh run,
    and no regression test passing on the baseline to fail with the patch."""
    if len(replays) != required:
        last = replays[-1].get("exit_code") if replays else None
        return Decision("FIX_REJECTED", f"Replay {len(replays)} of {required} on the patched code exited {last}, so the bug is still there")
    runs = [*replays, *(run for run in (regression_patched, regression_baseline) if run)]
    for run in runs:
        if (run.get("destruction") or {}).get("destroyed") is not True:
            return Decision("FAILED", "A container's destruction wasn't confirmed")
        attestation = run.get("attestation") or {}
        if not attestation or attestation.get("mismatches"):
            return Decision("FAILED", "A sandbox failed attestation")
    for index, replay in enumerate(replays, start=1):
        if replay.get("script_sha256") != script_sha256 or replay.get("applied_patch_sha256") != patch_sha256:
            return Decision("FAILED", f"Replay {index} didn't run the frozen script on this patch")
        if replay.get("termination") != "exited" or replay.get("exit_code") != 0 or replay.get("symptom_observed") is not False:
            return Decision("FIX_REJECTED", f"Replay {index} on the patched code exited {replay.get('exit_code')}, not 0")
    if not regression_patched or not regression_baseline:
        return Decision("FIX_REJECTED", "The regression tests didn't run")
    for run in (regression_patched, regression_baseline):
        if run.get("termination") != "exited" or run.get("exit_code") not in (0, 1) or run.get("stdout_truncated"):
            return Decision("FIX_REJECTED", f"The regression run ended as {run.get('termination')}, exit {run.get('exit_code')}")
    if regression_patched.get("applied_patch_sha256") != patch_sha256:
        return Decision("FAILED", "The regression run didn't use this patch")
    for run in (regression_patched, regression_baseline):
        if run.get("canary_failed") is not True or run.get("exit_code") != 1:
            return Decision("FIX_REJECTED", "The built-in failing test didn't fail, so this regression result can't be trusted")
    baseline_total = (regression_baseline.get("tests_passed") or 0) + (regression_baseline.get("tests_failed") or 0)
    patched_total = (regression_patched.get("tests_passed") or 0) + (regression_patched.get("tests_failed") or 0)
    if not baseline_total or patched_total != baseline_total:
        return Decision("FIX_REJECTED", f"The patched run reported {patched_total} tests, and the current code reported {baseline_total}")
    new_failures = sorted(set(regression_patched.get("failed_tests") or []) - set(regression_baseline.get("failed_tests") or []))
    if new_failures:
        return Decision("FIX_REJECTED", f"{len(new_failures)} tests now fail: {', '.join(new_failures[:5])}")
    return Decision("FIX_VERIFIED", f"The frozen script passed {required} of {required} fresh replays on the patched code, with no new test failures")
