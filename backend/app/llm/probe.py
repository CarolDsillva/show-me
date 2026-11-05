"""Live capability check for candidate Vultr Serverless Inference models.

  python backend/app/llm/probe.py [model ...]

Reads VULTR_INFERENCE_API_KEY from the environment or .secrets/vultr.env, and never prints it.
Makes real, billed calls, kept very small. Tests what ReproFix needs: plain text, tool
calling, structured JSON, and reading a screenshot. The listed capabilities aren't proof.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib

BASE_URL = "https://api.vultrinference.com/v1"
DEFAULT_MODELS = ["glm-5.3", "qwen3.8-27b", "qwen3.8-flash-next"]
ROOT = pathlib.Path(__file__).resolve().parents[3]


def api_key() -> str:
    value = os.environ.get("VULTR_INFERENCE_API_KEY")
    if value:
        return value
    secrets = ROOT / ".secrets" / "vultr.env"
    if secrets.exists():
        for line in secrets.read_text(encoding="utf-8").splitlines():
            if line.startswith("VULTR_INFERENCE_API_KEY="):
                value = line.split("=", 1)[1].strip()
    if not value:
        sys.exit("VULTR_INFERENCE_API_KEY is missing. Copy deploy/env/vultr.env.example to .secrets/vultr.env.")
    return value


def chat(key: str, body: dict) -> tuple[dict, float]:
    request = urllib.request.Request(f"{BASE_URL}/chat/completions", data=json.dumps(body).encode(), method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Bearer {key}")
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read())
    return payload, time.monotonic() - started


def red_square_png() -> str:
    """A 32x32 solid red PNG, so the vision check has a known answer."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    row = b"\x00" + b"\xff\x00\x00" * 32
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * 32))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode()


CLICK_TOOL = {
    "type": "function",
    "function": {
        "name": "click",
        "description": "Click an element in the browser.",
        "parameters": {"type": "object", "properties": {"selector": {"type": "string"}}, "required": ["selector"]},
    },
}


def message(payload: dict) -> dict:
    return payload["choices"][0]["message"]


def check_plain(key: str, model: str) -> tuple[bool, str, float, dict]:
    payload, seconds = chat(key, {"model": model, "max_tokens": 200, "messages": [{"role": "user", "content": "Reply with exactly: OK"}]})
    text = message(payload).get("content") or ""
    return "OK" in text, text[:60], seconds, payload.get("usage", {})


def check_tools(key: str, model: str) -> tuple[bool, str, float, dict]:
    payload, seconds = chat(key, {
        "model": model, "max_tokens": 400, "tools": [CLICK_TOOL], "tool_choice": "required",
        "messages": [{"role": "user", "content": "Click the element with selector #save."}],
    })
    calls = message(payload).get("tool_calls") or []
    good = bool(calls) and calls[0]["function"]["name"] == "click" and "#save" in calls[0]["function"]["arguments"]
    return good, json.dumps(calls)[:100], seconds, payload.get("usage", {})


def check_json(key: str, model: str) -> tuple[bool, str, float, dict]:
    schema = {
        "name": "action", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False, "required": ["action", "selector"],
            "properties": {"action": {"type": "string", "enum": ["click", "fill"]}, "selector": {"type": "string"}},
        },
    }
    payload, seconds = chat(key, {
        "model": model, "max_tokens": 400, "response_format": {"type": "json_schema", "json_schema": schema},
        "messages": [{"role": "user", "content": "Return the action to click #save."}],
    })
    text = message(payload).get("content") or ""
    try:
        parsed = json.loads(text)
        good = parsed.get("action") == "click" and "#save" in parsed.get("selector", "")
    except json.JSONDecodeError:
        good = False
    return good, text[:80], seconds, payload.get("usage", {})


def check_vision(key: str, model: str) -> tuple[bool, str, float, dict]:
    payload, seconds = chat(key, {
        "model": model, "max_tokens": 300,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "What color fills this image? Answer with one word."},
            {"type": "image_url", "image_url": {"url": red_square_png()}},
        ]}],
    })
    text = message(payload).get("content") or ""
    return "red" in text.lower(), text[:60], seconds, payload.get("usage", {})


CHECKS = {"plain": check_plain, "tools": check_tools, "json": check_json, "vision": check_vision}


def main() -> int:
    key = api_key()
    models = sys.argv[1:] or DEFAULT_MODELS
    failed = False
    for model in models:
        print(f"\n{model}")
        for name, check in CHECKS.items():
            try:
                good, detail, seconds, usage = check(key, model)
                total = usage.get("total_tokens", "?")
                print(f"  {'PASS' if good else 'FAIL'}  {name:7} {seconds:6.1f}s  {total} tokens  {detail!r}")
                failed = failed or not good
            except urllib.error.HTTPError as error:
                print(f"  FAIL  {name:7} HTTP {error.code}: {error.read().decode(errors='replace')[:200]}")
                failed = True
            except Exception as error:  # A failed check is a result to report, not a crash.
                print(f"  FAIL  {name:7} {type(error).__name__}: {error}")
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
