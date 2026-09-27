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


def chat(req: ChatRequest) -> str:
    """Route via LiteLLM if configured, else deterministic stub for tests/dev."""
    if os.getenv("KETTLE_LIVE_LLM") != "1":
        return f"[stub:{req.model}] {req.user[:200]}"
    import litellm  # lazy so tests don't require keys

    resp = litellm.completion(
        model=req.model,
        messages=[{"role": "system", "content": req.system}, {"role": "user", "content": req.user}],
        max_tokens=req.max_tokens,
    )
    return resp.choices[0].message.content or ""
