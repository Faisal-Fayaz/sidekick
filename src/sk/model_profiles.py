"""Per-model capability profiles: known facts about models, committed as data.

The agent loop already learns at runtime (_tools_unsupported in agent.py:
one wasted 400 round-trip teaches it a model can't do native tools). These
profiles are the static counterpart — what we already know, so the first
turn doesn't pay tuition. Unknown models fall back to DEFAULT_PROFILE,
which preserves current behavior exactly.

Fields:
- native_tools: model follows OpenAI function-calling (False → start in
  text-JSON mode immediately, no probing 400).
- json_discipline: high | medium | low — how reliably text-emitted tool
  JSON parses. (Consumed by future prompt tuning; recorded now.)
- max_parallel: concurrent tool budget for _run_tools_batch. Weak models
  serialize more; strong ones fan out.
- note: one-line human rationale (shows in router reason strings).

Only verified entries live here (same rule as config.TIERS). To add one:
reproduce the behavior twice, then add the row + a test.
"""

from __future__ import annotations

DEFAULT_PROFILE: dict[str, object] = {
    "native_tools": True,
    "json_discipline": "high",
    "max_parallel": 4,
    "note": "unprofiled model: current heuristics apply",
}

PROFILES: dict[str, dict[str, object]] = {
    "llama3.2:3b": {
        "native_tools": False,
        "json_discipline": "medium",
        "max_parallel": 2,
        "note": "3B class: text-JSON only, keep batches small",
    },
    "qwen2.5-coder:7b": {
        "native_tools": True,
        "json_discipline": "high",
        "max_parallel": 4,
        "note": "reliable native tools + clean JSON fallback",
    },
}


def match_profile(model_id: str) -> dict[str, object]:
    """Best profile for a model id (case-insensitive substring). Never raises."""
    try:
        low = (model_id or "").strip().lower()
    except Exception:
        return dict(DEFAULT_PROFILE)
    if not low:
        return dict(DEFAULT_PROFILE)
    for key, profile in PROFILES.items():
        if key.lower() in low or low in key.lower():
            return dict(profile)
    return dict(DEFAULT_PROFILE)


def native_tools_for(model_id: str) -> bool:
    """Whether the model follows native function calling. Unknown → True."""
    try:
        return bool(match_profile(model_id).get("native_tools", True))
    except Exception:
        return True


def max_parallel_for(model_id: str) -> int:
    """Concurrent tool budget for the model. Unknown → 4. Always >= 1."""
    try:
        return max(1, int(str(match_profile(model_id).get("max_parallel", 4))))
    except Exception:
        return 4
