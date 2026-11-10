"""Vultr's listed per-token prices, read live from the public model list.

Cost figures use these rates. They leave out any subscription fee and the VM, so they're an
estimate of model usage only. If the list can't be read, the cost is unknown and shown as such.
"""

from __future__ import annotations

import httpx

from app.core.config import INFERENCE_BASE_URL


def _price(modalities: object, kind: str) -> float | None:
    for modality in modalities if isinstance(modalities, list) else []:
        for entry in modality.get("pricing") or [] if isinstance(modality, dict) else []:
            if entry.get("type") == kind and entry.get("unit") == "token":
                return float(entry["cost_usd"])
    return None


def parse_prices(payload: dict) -> dict[str, tuple[float, float]]:
    prices = {}
    for model in payload.get("data") or []:
        try:
            prompt = _price(model.get("input_modalities"), "prompt")
            completion = _price(model.get("output_modalities"), "completion")
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
        if prompt is not None and completion is not None:
            prices[model["id"]] = (prompt, completion)
    return prices


def fetch_prices(transport: httpx.BaseTransport | None = None) -> dict[str, tuple[float, float]]:
    try:
        with httpx.Client(timeout=20, transport=transport) as client:
            response = client.get(f"{INFERENCE_BASE_URL}/models")
            response.raise_for_status()
            return parse_prices(response.json())
    except (httpx.HTTPError, ValueError):
        return {}


def call_cost(prices: dict[str, tuple[float, float]], model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    rate = prices.get(model)
    if rate is None:
        return None
    return prompt_tokens * rate[0] + completion_tokens * rate[1]
