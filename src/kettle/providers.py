"""LLM provider abstraction (LiteLLM-backed). Activities call this; workflows never do."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class ChatRequest:
    model: str
    system: str
    user: str
    max_tokens: int = 2000


def chat(req: ChatRequest, *, timeout_s: int = 60, num_retries: int = 3) -> str:
    """Always call the configured LLM via LiteLLM — no stub path.

    Requires provider keys for the requested model (e.g. ANTHROPIC_API_KEY,
    OPENAI_API_KEY). Retries transient failures with backoff, then raises
    RuntimeError with model context.
    """
    import time

    import litellm  # lazy so import-time stays light

    last: Exception | None = None
    for attempt in range(max(num_retries, 1)):
        try:
            resp = litellm.completion(
                model=req.model,
                messages=[
                    {"role": "system", "content": req.system},
                    {"role": "user", "content": req.user},
                ],
                max_tokens=req.max_tokens,
                timeout=timeout_s,
            )
            content = resp.choices[0].message.content or ""
            if not content.strip():
                raise RuntimeError(f"empty completion from {req.model}")
            return content
        except Exception as exc:  # noqa: BLE001 — mapped below with context
            last = exc
            time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"LLM call failed for {req.model} after {num_retries} tries: {last}")


# Central model map — one-line swap per stage. Review vendor must differ
# from implement so the two don't share blind spots (cross-harness rule).
MODELS: dict[str, str] = {
    "coordinator": "anthropic/claude-sonnet-4-6",
    "triage": "anthropic/claude-haiku-4-5",
    "spec": "anthropic/claude-sonnet-4-6",
    "implement": "anthropic/claude-sonnet-4-6",
    "review": "openai/gpt-5-codex",
}

# Rough per-1k-token USD for cost tracking (updated periodically).
PRICE_PER_1K: dict[str, float] = {
    "anthropic/claude-haiku-4-5": 0.001,
    "anthropic/claude-sonnet-4-6": 0.006,
    "openai/gpt-5-codex": 0.006,
}


def model_for(stage: str) -> str:
    return os.getenv(f"MODEL_{stage.upper()}", MODELS.get(stage, MODELS["coordinator"]))


def vendor_of(model: str) -> str:
    return model.split("/")[0] if "/" in model else model


def estimate_cost_usd(model: str, tokens: int) -> float:
    return PRICE_PER_1K.get(model, 0.003) * tokens / 1000.0
