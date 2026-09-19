"""Auto model router: deterministic fast/smart pick from task text. No LLM, no deps.

fast = llama3.2:3b (2GB, instant, chat/fetch/recall)
smart = qwen2.5-coder:7b (tools, code, writes, grounding questions)
"""

from __future__ import annotations

import re

FAST_MODEL = "llama3.2:3b"
SMART_MODEL = "qwen2.5-coder:7b"

SMART_WORDS = {
    "code", "coding", "refactor", "debug", "rewrite", "implement", "fix",
    "function", "class", "script", "write", "edit", "create", "scope",
    "architecture", "review", "explain", "error", "bug", "test", "tests",
    "device", "hardware", "llm", "model", "vram", "gpu", "ram",
    "project", "repo", "repository", "install", "run", "execute", "delete",
    "remove", "folder", "directory", "file",
}

PATH_HINT = re.compile(r"(~/|\.\w{1,5}\b|/home/|[\w\-./]+\.(py|ts|tsx|js|rs|go|md|toml|json|yaml)\b)")


def pick_model(task: str, default: str = FAST_MODEL) -> tuple[str, str]:
    """Return (model, reason). Pure function, safe to unit test."""
    t = (task or "").lower()
    if len(t.strip()) < 3:
        return (default, "empty task, default")
    no_urls = re.sub(r"https?://\S+", " ", task)
    if PATH_HINT.search(no_urls):
        return (SMART_MODEL, "mentions paths/files")
    hits = sum(1 for w in SMART_WORDS if re.search(rf"\b{re.escape(w)}\b", t))
    if hits >= 1 and len(t.split()) > 2:
        return (SMART_MODEL, f"keyword ({hits} code/task terms)")
    if hits >= 2:
        return (SMART_MODEL, f"keywords ({hits})")
    return (FAST_MODEL, "chit-chat/fetch/recall")
