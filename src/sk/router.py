"""Auto model router: deterministic fast/smart pick from task text. No LLM, no deps.

Single source for tier decision; concrete names live in config.TIERS.
FAST_MODEL/SMART_MODEL are ollama-tier compat constants (do not hard-code
elsewhere — import TIERS or provider_tier instead).
"""

from __future__ import annotations

import re

try:
    from .config import TIERS
except Exception:  # pragma: no cover - import cycle guard in tests
    TIERS = {}  # type: ignore[assignment]

FAST_MODEL = TIERS.get("ollama", {}).get("fast", "llama3.2:3b") if TIERS else "llama3.2:3b"
SMART_MODEL = TIERS.get("ollama", {}).get("smart", "qwen2.5-coder:7b") if TIERS else "qwen2.5-coder:7b"

SMART_WORDS = {
    "code", "coding", "refactor", "debug", "rewrite", "implement", "fix",
    "function", "class", "script", "write", "edit", "create", "scope",
    "architecture", "review", "explain", "error", "bug", "test", "tests",
    "device", "hardware", "llm", "model", "vram", "gpu", "ram",
    "project", "repo", "repository", "install", "run", "execute", "delete",
    "remove", "folder", "directory", "file",
}

PATH_HINT = re.compile(r"(~/|\.\w{1,5}\b|/home/|[\w\-./]+\.(py|ts|tsx|js|rs|go|md|toml|json|yaml)\b)")


def pick_tier(task: str) -> tuple[str, str]:
    """Return (tier, reason) where tier is 'fast' or 'smart'. Pure function."""
    t = (task or "").lower()
    if len(t.strip()) < 3:
        return ("fast", "empty task, default")
    no_urls = re.sub(r"https?://\S+", " ", task)
    if PATH_HINT.search(no_urls):
        return ("smart", "mentions paths/files")
    hits = sum(1 for w in SMART_WORDS if re.search(rf"\b{re.escape(w)}\b", t))
    if hits >= 1 and len(t.split()) > 2:
        return ("smart", f"keyword ({hits} code/task terms)")
    if hits >= 2:
        return ("smart", f"keywords ({hits})")
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
