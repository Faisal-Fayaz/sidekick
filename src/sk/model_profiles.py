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
- max_tokens: per-turn generation cap. Local class stays tight (VRAM);
  frontier class gets room for whole-file tool calls.
- max_steps: loop iterations, or None to defer to user config. Local class
  pins the lean default; frontier class raises it.
- note: one-line human rationale (shows in router reason strings).

Only verified entries live here (same rule as config.TIERS). Budget rows
are policy, not observed behavior: local rows lock today's constants so
regressions show in tests; frontier rows encode the tested whole-file
turns. To add one: reproduce the behavior twice, then add the row + a test.
"""

from __future__ import annotations

LOCAL_TOKENS = 350
CLOUD_TOKENS = 800
FRONTIER_TOKENS = 2000
FRONTIER_STEPS = 15

# Total prompt window per class. Local is deliberately small: the KV cache is
# VRAM, and these run on machines where the model itself is CPU-offloaded.
#
# Measured on a real build with no auto-context and two tiny history messages:
# system prompt ~1970 tok + tool schemas ~1699 tok = ~3670 tok before the model
# has read anything. Against the old hardcoded num_ctx of 4096 that left ~324
# tokens for the entire conversation, so the budget could not be honoured at any
# setting (#308).
LOCAL_WINDOW = 16384
CLOUD_WINDOW = 128000
FRONTIER_WINDOW = 200000

DEFAULT_PROFILE: dict[str, object] = {
    "native_tools": True,
    "json_discipline": "high",
    "max_parallel": 4,
    "max_tokens": None,  # None → provider default (no behavior change)
    "max_steps": None,  # None → user config (no behavior change)
    "context_window": None,  # None → class default (no behavior change)
    "note": "unprofiled model: current heuristics apply",
}

PROFILES: dict[str, dict[str, object]] = {
    "llama3.2:3b": {
        "native_tools": False,
        "json_discipline": "medium",
        "max_parallel": 2,
        "max_tokens": LOCAL_TOKENS,
        "max_steps": 5,
        "context_window": LOCAL_WINDOW,
        "note": "3B class: text-JSON only, keep batches small",
    },
    "qwen2.5-coder:7b": {
        "native_tools": True,
        "json_discipline": "high",
        "max_parallel": 4,
        "max_tokens": LOCAL_TOKENS,
        "max_steps": 5,
        "context_window": LOCAL_WINDOW,
        "note": "reliable native tools + clean JSON fallback",
    },
    "gpt-4o": {
        "native_tools": True,
        "json_discipline": "high",
        "max_parallel": 4,
        "max_tokens": FRONTIER_TOKENS,
        "max_steps": FRONTIER_STEPS,
        "context_window": FRONTIER_WINDOW,
        "note": "frontier class: whole-file turns fit",
    },
    "claude-sonnet-5": {
        "native_tools": True,
        "json_discipline": "high",
        "max_parallel": 4,
        "max_tokens": FRONTIER_TOKENS,
        "max_steps": FRONTIER_STEPS,
        "context_window": FRONTIER_WINDOW,
        "note": "frontier class: whole-file turns fit",
    },
    "deepseek-chat": {
        "native_tools": True,
        "json_discipline": "high",
        "max_parallel": 4,
        "max_tokens": FRONTIER_TOKENS,
        "max_steps": FRONTIER_STEPS,
        "context_window": FRONTIER_WINDOW,
        "note": "frontier class: whole-file turns fit",
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


def max_tokens_for(model_id: str, provider: str = "") -> int:
    """Per-turn generation cap. Profile value wins; else provider default
    (tight on local CPU offload, roomy on cloud). Never raises."""
    try:
        profiled = match_profile(model_id).get("max_tokens", None)
        if profiled is not None:
            return max(64, int(str(profiled)))
    except Exception:
        pass
    return LOCAL_TOKENS if (provider or "") in ("ollama", "lmstudio") else CLOUD_TOKENS


def context_window_for(model_id: str, provider: str = "", override: int = 0) -> int:
    """Total prompt window (input + output) for a model. Never raises.

    Precedence: explicit user override, then the profile, then a class default.
    The same number feeds Ollama's `num_ctx` and the prompt budget (#308), so
    the two cannot drift apart the way the old hardcoded 4096 did.
    """
    try:
        v = int(override or 0)
        if v > 0:
            return max(2048, v)
    except Exception:
        pass
    try:
        profiled = match_profile(model_id).get("context_window", None)
        if profiled is not None:
            return max(2048, int(str(profiled)))
    except Exception:
        pass
    return LOCAL_WINDOW if (provider or "") in ("ollama", "lmstudio") else CLOUD_WINDOW


def max_steps_for(model_id: str) -> int | None:
    """Loop iterations from the profile, or None to defer to user config."""
    try:
        profiled = match_profile(model_id).get("max_steps", None)
        return max(1, int(str(profiled))) if profiled is not None else None
    except Exception:
        return None


def effective_max_steps(model_id: str, cfg) -> int:
    """Loop iterations for this turn. Precedence: explicit user config wins,
    else profile, else the configured default. Never raises."""
    try:
        if bool(getattr(cfg, "max_steps_custom", False)):
            return max(1, int(getattr(cfg, "max_steps", 5)))
        profiled = max_steps_for(model_id)
        if profiled is not None:
            return profiled
        return max(1, int(getattr(cfg, "max_steps", 5)))
    except Exception:
        return 5
