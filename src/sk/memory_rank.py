"""Pure memory helpers: ranking, similarity, settings and proposals (#493).

No database access and no writes live here. `store.py` calls these functions
with the rows it reads; the proposal extractor returns candidate facts as data
for the agent to *offer* through the existing `remember` tool. FTS5 is the floor
(relevance still finds candidates); recency and use count decide the order.
"""

from __future__ import annotations

import math
import os
import re
import time
from difflib import SequenceMatcher

DEFAULT_HALF_LIFE_DAYS = 30.0
DEFAULT_DEDUP_THRESHOLD = 0.85
MIN_HALF_LIFE_DAYS = 1.0
MIN_DEDUP_THRESHOLD = 0.5
MAX_DEDUP_THRESHOLD = 1.0


def settings() -> dict[str, float]:
    """Tunables from the global config or environment; invalid values fall back.

    A project file must not set these: a repository cannot change how the user's
    own memory is ranked or merged, any more than it can set policy (#482).
    """
    from .config import CONFIG_PATH

    vals: dict[str, object] = {}
    try:
        import tomllib

        with open(CONFIG_PATH, "rb") as handle:
            loaded = tomllib.load(handle)
        if isinstance(loaded, dict):
            vals = loaded
    except Exception:
        pass

    def number(key: str, env: str, default: float, low: float, high: float) -> float:
        raw = os.getenv(env, "").strip() or str(vals.get(key, default))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(value) or value < low or value > high:
            return default
        return value

    return {
        "half_life_days": number(
            "memory_half_life_days",
            "SIDEKICK_MEMORY_HALF_LIFE_DAYS",
            DEFAULT_HALF_LIFE_DAYS,
            MIN_HALF_LIFE_DAYS,
            3650.0,
        ),
        "dedup_threshold": number(
            "memory_dedup_threshold",
            "SIDEKICK_MEMORY_DEDUP_THRESHOLD",
            DEFAULT_DEDUP_THRESHOLD,
            MIN_DEDUP_THRESHOLD,
            MAX_DEDUP_THRESHOLD,
        ),
    }


def normalise(text: str) -> str:
    """Canonical form for duplicate detection: lowercase, one space, no punctuation."""
    lowered = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())
    return " ".join(lowered.split())


def similarity(left: str, right: str) -> float:
    a, b = normalise(left), normalise(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def importance_score(uses: int) -> float:
    """A fact recalled five times outranks an otherwise equal one recalled once."""
    return 1.0 + math.log1p(max(0, int(uses or 0))) / math.log(2.0)


def recency_score(
    ts: float, now: float | None = None, half_life_days: float = DEFAULT_HALF_LIFE_DAYS
) -> float:
    """Exponential decay with a half-life in days; never boosts a future stamp."""
    current = float(now if now is not None else time.time())
    age_days = max(0.0, (current - float(ts or 0.0)) / 86400.0)
    return 2.0 ** (-age_days / max(1.0, half_life_days))


def rank_score(
    relevance: float,
    ts: float,
    uses: int,
    *,
    now: float | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> float:
    """FTS relevance × recency × importance. `relevance` is a non-negative quality in [0, 1]."""
    return (
        max(0.0, float(relevance))
        * recency_score(ts, now=now, half_life_days=half_life_days)
        * importance_score(uses)
    )


# A proposal is a *suggestion*, never a save. Patterns are deliberately narrow:
# a durable statement about the user or their work, not every sentence they say.
_PROPOSAL_PATTERNS = (
    re.compile(r"^(?:please\s+)?remember\s+that\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?remember\s+(.+)$", re.IGNORECASE),
    re.compile(r"^my\s+(.+)$", re.IGNORECASE),
    re.compile(r"^i\s+(?:prefer|use|work on|work with)\s+(.+)$", re.IGNORECASE),
)


def proposals(text: str, limit: int = 3) -> list[str]:
    """Candidate durable facts from a user turn. Never writes to the store."""
    out: list[str] = []
    for line in str(text or "").splitlines():
        sentence = line.strip().rstrip(".!").strip()
        if not sentence or len(sentence) > 200:
            continue
        for pattern in _PROPOSAL_PATTERNS:
            match = pattern.match(sentence)
            if not match:
                continue
            groups = [g.strip() for g in match.groups() if g and g.strip()]
            fact = " ".join(groups)
            if len(fact) >= 3 and fact.lower() not in {f.lower() for f in out}:
                out.append(fact[:200])
            break
        if len(out) >= max(0, limit):
            break
    return out[: max(0, limit)]
