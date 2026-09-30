"""Cache-discipline tests (refs #276): canonical request order + stable schemas.

Prompt caches key on exact prefixes: system → tool schemas → project
context → history → fresh user/tool output. These tests lock the order
and the schema determinism so refactors cannot silently bust the cache.
Offline only (conftest isolates config + history DB).
"""

import sk.plugins as plugins
from sk.agent import _drop_stale_control, build_messages
from sk.config import Config
from sk.tools import registry


def _cfg():
    return Config(model="t", base_url="http://x/v1", api_key="x", max_steps=1, temperature=0.0)


def _names(schema):
    return [e.get("function", {}).get("name", "") for e in schema]


def test_tool_schema_extras_name_sorted_and_stable(monkeypatch):
    """Plugin/MCP load order must never reorder the schema (cache-bust)."""
    import sk.mcp_client as mcp_client

    unsorted = [
        {"type": "function", "function": {"name": "zeta_tool", "description": "z"}},
        {"type": "function", "function": {"name": "alpha_tool", "description": "a"}},
    ]
    monkeypatch.setattr(plugins, "schema_extra", lambda: list(unsorted))
    monkeypatch.setattr(mcp_client, "mcp_schema_extra", lambda: list(reversed(unsorted)))
    first, second = registry.tools_schema(), registry.tools_schema()
    assert first == second  # repeated calls identical
    builtin_len = len(registry.TOOLS_SCHEMA)
    assert _names(first)[:builtin_len] == _names(registry.TOOLS_SCHEMA)  # builtins first, stable
    # each extra source name-sorted independently (grouping preserved),
    # so load order / server flaps never reorder the schema
    assert _names(first)[builtin_len : builtin_len + 2] == ["alpha_tool", "zeta_tool"]
    assert _names(first)[builtin_len + 2 :] == ["alpha_tool", "zeta_tool"]


def test_build_messages_canonical_order():
    """System first, history middle, current user turn last."""
    hist = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]
    msgs = build_messages("fresh question", hist, _cfg())
    assert msgs[0]["role"] == "system"
    assert [m["content"] for m in msgs[1:-1]] == ["earlier question", "earlier answer"]
    last = msgs[-1]
    assert last["role"] == "user" and "fresh question" in str(last["content"])


def test_per_turn_list_mutations_never_touch_stored_history():
    """Control-tag cleanup + turn appends act on the fresh list only."""
    hist = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]
    snapshot = [dict(m) for m in hist]
    msgs = build_messages("q2", hist, _cfg())
    msgs.append({"role": "user", "content": "[sidekick-control] stale note"})
    msgs.append({"role": "user", "content": "[sidekick-control] newer note"})
    _drop_stale_control(msgs)  # drops the stale note from the turn list
    assert hist == snapshot  # stored history untouched
    assert len(hist) == 2
