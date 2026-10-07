"""Auto model router: deterministic fast/smart pick from task text. No LLM, no deps.

Single source for tier decision; concrete names live in config.TIERS.
FAST_MODEL/SMART_MODEL are ollama-tier compat constants (do not hard-code
elsewhere — import TIERS or provider_tier instead).
"""

from __future__ import annotations

import re

from .config import TIERS, provider_tier
from .model_profiles import match_profile, max_parallel_for, native_tools_for

FAST_MODEL = TIERS.get("ollama", {}).get("fast", "llama3.2:3b") if TIERS else "llama3.2:3b"
SMART_MODEL = (
    TIERS.get("ollama", {}).get("smart", "qwen2.5-coder:7b") if TIERS else "qwen2.5-coder:7b"
)

SMART_WORDS = {
    "code",
    "coding",
    "refactor",
    "debug",
    "rewrite",
    "implement",
    "fix",
    "function",
    "class",
    "script",
    "write",
    "edit",
    "create",
    "scope",
    "architecture",
    "review",
    "explain",
    "error",
    "bug",
    "test",
    "tests",
    "device",
    "hardware",
    "llm",
    "model",
    "vram",
    "gpu",
    "ram",
    "project",
    "repo",
    "repository",
    "install",
    "run",
    "execute",
    "delete",
    "remove",
    "folder",
    "directory",
    "file",
}

PATH_HINT = re.compile(
    r"(~/|\.\w{1,5}\b|/home/|[\w\-./]+\.(py|ts|tsx|js|rs|go|md|toml|json|yaml)\b)"
)

# Generic verbs that only signal real work alongside another task term.
# A lone generic hit ("write me a poem") stays fast; two hits of any kind
# ("write a backup script") go smart. Fixes the measured #275 over-route.
GENERIC_WORDS = {"write", "file", "run", "execute", "create", "explain"}

# Loop-machinery verdicts that mark a turn as failed/low-confidence and
# eligible for one fast→smart retry. should_escalate matches on both of these,
# and agent.py / anthropic_backend.py produce them -- so this tuple is the
# contract, not documentation. It was previously defined and then ignored, with
# both strings re-typed inline in should_escalate, which meant renaming a
# producer would silently disable fast→smart escalation with no test failing.
# Never raises.
EXHAUSTION_MARKERS = ("(max steps reached)", "Accomplished:")


def pick_tier(task: str) -> tuple[str, str]:
    """Return (tier, reason) where tier is 'fast' or 'smart'. Pure function."""
    t = (task or "").lower()
    if len(t.strip()) < 3:
        return ("fast", "empty task, default")
    no_urls = re.sub(r"https?://\S+", " ", task)
    if PATH_HINT.search(no_urls):
        return ("smart", "mentions paths/files")
    hits = {w for w in SMART_WORDS if re.search(rf"\b{re.escape(w)}\b", t)}
    specific = hits - GENERIC_WORDS
    if specific and len(t.split()) > 2:
        return ("smart", f"keyword ({len(hits)} code/task terms)")
    if len(hits) >= 2:
        return ("smart", f"keywords ({len(hits)})")
    return ("fast", "chit-chat/fetch/recall")


def pick_model(task: str, default: str | None = None) -> tuple[str, str]:
    """Return (model, reason). Compat wrapper: tier -> ollama concrete name.

    Prefer pick_tier() + provider_tier() for non-ollama providers.
    """
    tier, reason = pick_tier(task)
    if tier == "smart":
        return (SMART_MODEL, reason)
    if default is not None:
        # explicit default wins for fast tier (preserves old signature)
        return (default, reason)
    return (FAST_MODEL, reason)


def route(task: str, provider: str = "ollama", model_override: str = "") -> dict[str, object]:
    """Profile-aware routing decision. Pure function, safe to unit test.

    Tier comes from task text (pick_tier); the concrete model from the
    override or the provider tier map; the capability profile from the
    model id (unknown models get DEFAULT_PROFILE = current behavior).
    Returns {tier, reason, model, profile, native_tools, max_parallel}.
    """
    tier, reason = pick_tier(task)
    override = (model_override or "").strip()
    if override:
        model = override
        reason = f"{reason} (model override)"
    elif provider in ("ollama", "lmstudio", "custom"):
        model = SMART_MODEL if tier == "smart" else FAST_MODEL
    else:
        model = provider_tier(provider, tier, "")
        if not model:
            model = SMART_MODEL if tier == "smart" else FAST_MODEL
            reason = f"{reason} (tier unverified for {provider})"
    profile = match_profile(model)
    return {
        "tier": tier,
        "reason": reason,
        "model": model,
        "profile": profile,
        "native_tools": native_tools_for(model),
        "max_parallel": max_parallel_for(model),
    }


def should_escalate(answer: str) -> bool:
    """True when a fast-tier turn failed and deserves one smart retry.

    Signals: empty answer, the hard "(max steps reached)" fallback, or the
    exhaustion synthesis ("Accomplished:" 3-line report) which means the step
    budget died without a final answer. Pure function, never raises.
    """
    try:
        t = (answer or "").strip()
        if not t:
            return True
        return t.startswith(EXHAUSTION_MARKERS[1]) or EXHAUSTION_MARKERS[0] in t
    except Exception:
        return False
