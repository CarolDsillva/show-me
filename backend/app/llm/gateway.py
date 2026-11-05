"""The only module that calls a model. Every call goes to Vultr Serverless Inference."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from typing import Callable

import httpx

from app.core.config import INFERENCE_BASE_URL

MAX_TOKENS = 3000


class BudgetExceeded(RuntimeError):
    """The token cap was reached. No more model calls for this task."""


class LLMError(RuntimeError):
    """Both models failed, or returned nothing usable."""


@dataclasses.dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclasses.dataclass
class CallRecord:
    base_url: str
    model: str
    prompt_sha256: str
    response_sha256: str
    prompt_tokens: int
    completion_tokens: int
    at: str


class Gateway:
    def __init__(
        self,
        api_key: str,
        primary_model: str,
        fallback_model: str,
        token_budget: int,
        on_call: Callable[[CallRecord], None],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.models = (primary_model, fallback_model)
        self.token_budget = token_budget
        self.tokens_used = 0
        self.on_call = on_call
        self.http = httpx.Client(
            base_url=INFERENCE_BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=120,
            transport=transport,
        )

    def call_tool(self, messages: list[dict], tool: dict) -> ToolCall:
        """Ask for one call to `tool`. Tries the primary, then the fallback, once each."""
        if self.tokens_used >= self.token_budget:
            raise BudgetExceeded(f"Used {self.tokens_used} of {self.token_budget} tokens")
        name = tool["function"]["name"]
        failures = []
        for model in self.models:
            try:
                return self._call(model, messages, tool, name)
            except (httpx.HTTPError, LLMError, ValueError, KeyError, IndexError, TypeError) as error:
                failures.append(f"{model}: {type(error).__name__}: {str(error)[:200]}")
        raise LLMError("; ".join(failures))

    def _call(self, model: str, messages: list[dict], tool: dict, name: str) -> ToolCall:
        body = {
            "model": model,
            "messages": messages,
            "tools": [tool],
            "tool_choice": {"type": "function", "function": {"name": name}},
            "max_tokens": MAX_TOKENS,
            "temperature": 0.1,
        }
        response = self.http.post("/chat/completions", json=body)
        response.raise_for_status()
        raw = response.content
        payload = response.json()
        usage = payload.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        self.tokens_used += prompt_tokens + completion_tokens
        self.on_call(CallRecord(
            base_url=INFERENCE_BASE_URL,
            model=model,
            prompt_sha256=hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
            response_sha256=hashlib.sha256(raw).hexdigest(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            at=dt.datetime.now(dt.timezone.utc).isoformat(),
        ))
        calls = payload["choices"][0]["message"].get("tool_calls") or []
        for call in calls:
            if call["function"]["name"] == name:
                arguments = json.loads(call["function"]["arguments"])
                if isinstance(arguments, dict):
                    return ToolCall(name, arguments)
        raise LLMError("The model didn't call the tool")
