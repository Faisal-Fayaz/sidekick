"""A tool call we cannot parse must never be posted as the answer (#367).

Observed on 2026-10-03: a model reached through OpenRouter emitted MiniMax
chat-template markup instead of a tool call, and Sidekick posted the markup
verbatim as the assistant's reply. Two failures at once — the user saw model
internals, *and* the three `read_file` calls the model meant to make were silently
dropped, so the turn ended having done none of them.

`_parse_text_tools` only finds JSON, so nothing downstream could catch it. The
"emit tools as ```json blocks" note only fires when native tools are OFF, which is
not the case for a model that usually calls tools natively — there was no path at
all.

Real captured residue below, verbatim from `~/.sidekick/history.db`.
"""

from __future__ import annotations

import sk.agent as agent_mod
from sk.agent import (
    KNOWN_TOOL_NAMES,
    MAX_RESIDUE_RETRIES,
    _parse_text_tools,
    _strip_tool_residue,
    _tool_residue,
)

# Verbatim from session tui-20261003-175539640461-0001, assistant message.
REAL_RESIDUE = (
    "I’ve confirmed this is C++17 + SFML, with most logic concentrated in "
    "`src/main.cpp` and `src/physics.cpp`. I’ll inspect their interfaces to "
    "distinguish a direct transliteration from a safer staged port."
    "]<]minimax[>[<tool_call>\n"
    ']<]minimax[>[<invoke name="read_file">'
    "]<]minimax[>[<path>/home/faisal/physics-simulator/src/physics.hpp>"
    "]<]minimax[>[</path>"
    ']<]minimax[>[<invoke name="read_file">'
    "]<]minimax[>[<path>/home/faisal/physics-simulator/src/main.cpp>"
    "]<]minimax[>[</path>"
    ']<]minimax[>[<invoke name="read_file">'
    "]<]minimax[>[<path>/home/faisal/physics-simulator/src/ui.hpp>"
    "]<]minimax[>[</path>"
    "]<]minimax[>[</invoke>"
    "]<]minimax[>[</parameter>"
    "]<]minimax[>[</invoke>"
)


# --- detection --------------------------------------------------------------


def test_detects_the_real_captured_residue():
    assert _tool_residue(REAL_RESIDUE) is True


def test_detects_each_marker_family():
    """`parameter` and `function` are deliberately absent: "A <parameter> is a
    placeholder in some frameworks" is ordinary prose, and a detector that eats
    legitimate answers is worse than no detector."""
    for s in (
        "<tool_call>",
        "<invoke name='read_file'>",
        "<invoke>",
        "</invoke>",
        "<]minimax[>[<",
        "<]something_else[>[<",
    ):
        assert _tool_residue(s) is True, s


def test_invoke_name_must_be_a_tool_we_have():
    """`<invoke name=...>` is only residue when the name is one of ours.

    Otherwise a model quoting an example of some *other* system's markup would
    have its answer eaten.
    """
    assert _tool_residue('<invoke name="read_file">x</invoke>') is True
    assert _tool_residue('<invoke name="some_other_system_tool">x</invoke>') is False


def test_no_false_positive_on_ordinary_prose():
    """A false positive would eat a legitimate answer, so this matters most."""
    for ok in (
        "I will not emit a tool_call block.",
        "The tool_call format is native to this model.",
        "Here is a <div> tag in my answer.",
        "Compare 3 < 5 and 7 > 2 in the output.",
        "Use ```json to call a tool.",
        "A <parameter> is a placeholder in some frameworks.",
        "",
        None,
    ):
        assert _tool_residue(ok) is False, repr(ok)


def test_markers_do_not_occur_in_our_own_prompt():
    """If these appeared in SYSTEM_PROMPT or the schemas, the detector could
    fire on the model echoing our own instructions back."""
    import json

    from sk.agent import SYSTEM_PROMPT
    from sk.tools.registry import TOOLS_SCHEMA

    for text in (SYSTEM_PROMPT, json.dumps(TOOLS_SCHEMA)):
        for marker in ("<tool_call>", "<invoke", "</invoke>", "minimax", "<parameter>"):
            assert marker not in text, marker


# --- stripping --------------------------------------------------------------


def test_strip_keeps_the_prose_that_came_before():
    out = _strip_tool_residue(REAL_RESIDUE)
    assert "C++17 + SFML" in out
    assert "minimax" not in out and "invoke" not in out
    assert "<" not in out


def test_strip_is_identity_on_clean_text():
    for ok in ("plain answer", "", "a < b and c > d"):
        assert _strip_tool_residue(ok) == ok


def test_known_tool_names_matches_the_parser():
    """The detector and the JSON parser must agree on what a tool is."""
    from sk.agent import _parse_text_tools

    sample = '[{"name": "read_file", "arguments": {"path": "/x"}}]'
    parsed = {n for n, _a in _parse_text_tools(sample)}
    assert parsed and parsed <= KNOWN_TOOL_NAMES


# --- the safety net, driven through run_agent -------------------------------


class _Chunk:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _cfg(tmp_path, monkeypatch):
    import sk.config as config_mod
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    cfg = config_mod.Config(
        provider="openrouter",
        model="stealth/space-bunny-alpha",
        base_url="",
        api_key="k",
        max_steps=5,
        temperature=0.0,
    )
    cfg.history_budget_tokens = 100_000
    return cfg


# Captured once, at import, before any patching. Re-reading
# `agent_mod._stream_chat` inside _run would wrap the *previous* lambda, so the
# second call in a test would nest two wrappers and consume two scripted replies.
_REAL_STREAM_CHAT = agent_mod._stream_chat
_REAL_RETRY = agent_mod._create_with_retry


def _run(cfg, monkeypatch, replies):
    """Drive run_agent over a scripted sequence of assistant replies."""
    seq = list(replies)
    state = {"tokens": [], "notes": [], "calls": 0}

    def transport(_p):
        state["calls"] += 1
        text = seq.pop(0) if seq else "final answer"
        yield _Chunk(choices=[{"delta": {"content": text}}])
        yield _Chunk(choices=[{"delta": {}, "finish_reason": "stop"}])

    monkeypatch.setattr(
        agent_mod,
        "_stream_chat",
        lambda *a, **kw: _REAL_STREAM_CHAT(*a, **{**kw, "transport": transport}),
    )
    monkeypatch.setattr(agent_mod, "_create_with_retry", lambda *a, **kw: iter([]))

    def on_token(t):
        state["tokens"].append(t)

    out = agent_mod.run_agent("go", [], cfg, on_token=on_token)
    return out, state, seq


def test_residue_is_never_posted_as_the_answer(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out, _state, _ = _run(cfg, monkeypatch, [REAL_RESIDUE, "Here is the answer you asked for."])
    assert "minimax" not in out
    assert "<invoke" not in out and "<tool_call>" not in out
    assert out == "Here is the answer you asked for."


def test_the_model_gets_another_step_and_a_corrective_turn(tmp_path, monkeypatch):
    """The point of the net: the model's intent is recoverable, not just hidden."""
    cfg = _cfg(tmp_path, monkeypatch)
    out, _state, leftover = _run(cfg, monkeypatch, [REAL_RESIDUE, "Recovered answer."])
    assert out == "Recovered answer."
    assert leftover == [], "the turn should not have exhausted its steps"


def test_prose_survives_when_the_model_never_recovers(tmp_path, monkeypatch):
    """Bounded retries: keep the prose, say what happened, never show markup."""
    cfg = _cfg(tmp_path, monkeypatch)
    out, state, _leftover = _run(cfg, monkeypatch, [REAL_RESIDUE] * 20)
    assert "minimax" not in out and "<invoke" not in out
    assert "C++17 + SFML" in out, "the prose before the markup should survive"
    assert "cannot parse" in out.lower()
    # one try, then MAX_RESIDUE_RETRIES corrections, then it stops
    assert state["calls"] == MAX_RESIDUE_RETRIES + 1, (
        f"used {state['calls']} model calls; the bound is {MAX_RESIDUE_RETRIES + 1}"
    )


def test_a_pure_markup_answer_reports_an_error_not_nothing(tmp_path, monkeypatch):
    """No prose at all: still say something rather than an empty turn."""
    cfg = _cfg(tmp_path, monkeypatch)
    bare = ']<]minimax[>[<invoke name="read_file">]<]minimax[>[</invoke>'
    out, state, _ = _run(cfg, monkeypatch, [bare] * 20)
    assert "minimax" not in out
    assert not out.startswith("]"), "template debris left in front of the error"
    assert out.strip(), "must not post an empty answer"
    assert "cannot parse" in out.lower()
    assert state["calls"] == MAX_RESIDUE_RETRIES + 1


def test_clean_answers_are_untouched(tmp_path, monkeypatch):
    """The regression that matters most: normal replies must not be affected."""
    cfg = _cfg(tmp_path, monkeypatch)
    for good in (
        "Here is a normal answer.",
        "I will not emit a tool_call block.",
        "Compare 3 < 5 and 7 > 2.",
        '```json\n{"note": "example only"}\n```',
    ):
        out, _state, leftover = _run(cfg, monkeypatch, [good])
        assert out == good, good
        assert leftover == []


def test_json_block_tool_calls_still_dispatch(tmp_path, monkeypatch):
    """The existing text-tool fallback must keep working alongside the net."""
    cfg = _cfg(tmp_path, monkeypatch)
    ran = []
    real_batch = agent_mod._run_tools_batch

    def spy(calls, *a, **kw):
        ran.extend(n for n, _ in calls)
        return real_batch(calls, *a, **kw)

    monkeypatch.setattr(agent_mod, "_run_tools_batch", spy)
    block = '[{"name": "read_file", "arguments": {"path": "/tmp/x"}}]'
    _run(cfg, monkeypatch, [block, "Read it."])
    assert "read_file" in ran, "the JSON fallback regressed"


def test_retries_are_bounded_by_a_constant():
    """The bound has to exist and be small; 2 is the documented choice."""
    assert MAX_RESIDUE_RETRIES == 2


# --- the name list must not drift from the schema (#376) -------------------
#
# The detector only treats `<invoke name=X>` as unparsed markup when X is a tool
# we actually have. While the name list was hand-maintained it was missing
# generate_image and shell_session, so markup naming either was NOT detected as
# residue and was posted to the user verbatim -- the exact failure #368 exists to
# prevent. Both are approval-gated, which is why including them is safe.


def test_known_tool_names_matches_the_schema_exactly():
    from sk.tools import TOOLS_SCHEMA

    schema_names = {str(t["function"]["name"]) for t in TOOLS_SCHEMA}
    assert KNOWN_TOOL_NAMES == frozenset(schema_names), (
        f"only in KNOWN_TOOL_NAMES: {sorted(KNOWN_TOOL_NAMES - schema_names)}; "
        f"only in TOOLS_SCHEMA: {sorted(schema_names - KNOWN_TOOL_NAMES)}"
    )


def test_every_schema_tool_is_named_by_the_detector():
    """Adding a tool must not require a second edit somewhere else."""
    from sk.tools import TOOLS_SCHEMA

    for t in TOOLS_SCHEMA:
        name = str(t["function"]["name"])
        assert _tool_residue(f'<invoke name="{name}">'), (
            f"{name} is in the schema but the residue detector does not recognise "
            f"markup naming it, so such output would be posted verbatim"
        )


def test_text_json_fallback_accepts_every_schema_tool():
    """The fallback must not quietly discard a tool the schema advertises."""
    from sk.tools import TOOLS_SCHEMA

    for t in TOOLS_SCHEMA:
        name = str(t["function"]["name"])
        parsed = _parse_text_tools(f'```json\n{{"name": "{name}", "arguments": {{}}}}\n```')
        assert parsed and parsed[0][0] == name, f"{name} was discarded by the fallback"


def test_generate_image_and_shell_session_are_detected_as_residue():
    """The two tools the drift had swallowed."""
    for name in ("generate_image", "shell_session"):
        assert _tool_residue(f'<invoke name="{name}"><arg>x</arg></invoke>')
        assert _tool_residue(f"<invoke name='{name}'>")
