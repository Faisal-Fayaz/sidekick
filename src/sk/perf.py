"""Token budgets and an offline prompt-overhead harness (#499).

The benchmark in `docs/probe-evals.py` measures a live model. This measures the
part we control without one: the fixed cost of every turn — system prompt, tool
schemas, injected context and the history the budget lets through. It is a
correctness guard as much as a performance one: prompt bloat silently pushes
history out of the context window.

No new runtime dependency. The model-driven latency numbers stay in the live
harness and are explicitly machine-dependent, never a CI gate.
"""

from __future__ import annotations

from sk.agent import _tool_schema_tokens, build_messages
from sk.config import Config
from sk.tokens import estimate_tokens

# Representative prompts: none asks for web or local auto-context, so measuring
# them requires no network and no model.
PROMPTS: dict[str, tuple[str, dict]] = {
    "chat": ("hi", {}),
    "run": ("list the files in this repo", {}),
    "review": ("review the current diff", {}),
    "explain": ("explain this function", {}),
    "check": ("run the checks", {}),
    "serve": ("summarise this repository", {"read_only": True}),
}

COMPONENT_BUDGETS: dict[str, int] = {
    "system": 2400,
    "tools": 3000,
    "fixed": 5600,
    "with_history": 9000,
}

# Every surface pays the same fixed tax, so each gets the same ceiling; keeping
# it per-surface makes a regression name the surface that broke it.
SURFACE_BUDGETS: dict[str, int] = {name: COMPONENT_BUDGETS["fixed"] for name in PROMPTS}
HISTORY_PAIRS = 10


def _cfg() -> Config:
    return Config(
        model="budget-probe", base_url="http://x/v1", api_key="x", max_steps=1, temperature=0.0
    )


def _history(pairs: int) -> list[dict]:
    out: list[dict] = []
    for i in range(pairs * 2):
        out.append(
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"turn {i}: " + ("filler text " * 60),
            }
        )
    return out


def measure_surface(
    name: str,
    *,
    cfg: Config | None = None,
    history_pairs: int = 0,
) -> dict:
    """Assemble one surface's prompt and return its token costs. No I/O beyond local context."""
    if name not in PROMPTS:
        raise KeyError(f"unknown surface {name!r}; choose from {', '.join(sorted(PROMPTS))}")
    prompt, flags = PROMPTS[name]
    messages = build_messages(prompt, _history(history_pairs), cfg or _cfg(), **flags)
    tokens = [estimate_tokens(str(m.get("content", ""))) for m in messages]
    system = tokens[0]
    # The last message is the fresh user turn. The untrusted fence, when
    # present, is the message right after the system prompt.
    from .trust import OPEN

    outside = tokens[1] if len(tokens) > 2 and OPEN in str(messages[1].get("content", "")) else 0
    tools = _tool_schema_tokens()
    total = sum(tokens) + tools
    history = max(0, total - system - outside - tools - tokens[-1])
    return {
        "surface": name,
        "system": system,
        "outside": outside,
        "tools": tools,
        "user": tokens[-1],
        "fixed": system + outside + tools,
        "history": history,
        "total": total,
        "budget": SURFACE_BUDGETS[name],
    }


def measure_all(*, cfg: Config | None = None) -> list[dict]:
    rows = [measure_surface(name, cfg=cfg) for name in PROMPTS]
    rows.append(measure_surface("chat", cfg=cfg, history_pairs=HISTORY_PAIRS))
    rows[-1]["surface"] = "chat+history"
    rows[-1]["budget"] = COMPONENT_BUDGETS["with_history"]
    return rows


def check(rows: list[dict]) -> list[str]:
    """Return one message per breach, empty when every surface is within budget."""
    problems: list[str] = []
    for row in rows:
        actual = (
            row.get("total", row.get("fixed", 0))
            if row["surface"] == "chat+history"
            else row["fixed"]
        )
        if actual > row["budget"]:
            problems.append(f"{row['surface']}: {actual} tokens > budget {row['budget']}")
    for component, limit in COMPONENT_BUDGETS.items():
        if component in ("fixed", "with_history"):
            continue
        peak = max((r.get(component, 0) for r in rows), default=0)
        if peak > limit:
            problems.append(f"{component}: peak {peak} tokens > budget {limit}")
    return problems
