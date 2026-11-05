"""Server-side settings. No request can change any of these."""

from __future__ import annotations

import dataclasses
import os
import pathlib

INFERENCE_BASE_URL = "https://api.vultrinference.com/v1"
ALLOWED_REPOSITORIES = frozenset({"CarolDsillva/linkding"})


@dataclasses.dataclass(frozen=True)
class Settings:
    inference_key: str
    github_token: str
    target_repo: str
    target_commit: str
    primary_model: str
    fallback_model: str
    data_dir: pathlib.Path
    sandboxd_socket: str
    fix_phase_enabled: bool
    commit: str
    operator_passcode: str
    max_explore_steps: int = 15
    replay_count: int = 3
    task_token_budget: int = 250_000
    max_patch_attempts: int = 3
    daily_token_budget: int = 2_000_000
    max_queue: int = 5
    tickets_per_ip_per_hour: int = 10
    drills_per_ip_per_hour: int = 30


def load_settings() -> Settings:
    target_repo = os.environ.get("TARGET_REPO", "")
    if target_repo not in ALLOWED_REPOSITORIES:
        raise RuntimeError("TARGET_REPO must be one of our own forks")
    return Settings(
        inference_key=os.environ["VULTR_INFERENCE_API_KEY"],
        github_token=os.environ["GITHUB_TOKEN"],
        target_repo=target_repo,
        target_commit=os.environ["TARGET_COMMIT"],
        primary_model=os.environ.get("PRIMARY_MODEL", "qwen3.8-flash-next"),
        fallback_model=os.environ.get("FALLBACK_MODEL", "qwen3.8-27b"),
        data_dir=pathlib.Path(os.environ.get("DATA_DIR", "/var/lib/reprofix")),
        sandboxd_socket=os.environ.get("SANDBOXD_SOCKET", "/run/reprofix/sandboxd.sock"),
        fix_phase_enabled=os.environ.get("FIX_PHASE_ENABLED", "false").lower() == "true",
        commit=os.environ.get("DEPLOYED_COMMIT", "unknown"),
        operator_passcode=os.environ["OPERATOR_PASSCODE"],
    )
