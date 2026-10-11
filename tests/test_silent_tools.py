"""A model that accepts `tools` but never calls one (silent-uncalling).

Observed live 2026-10-11 on an OpenRouter-hosted model: every request
returned HTTP 200, so `_tools_rejected` never fired, but the model answered
in prose on every turn — six of the eight most recent sessions ran zero
tools, and the turn burned its whole step budget shipping the exhaustion
recap instead of a result. `_note_silent_tool_turn` detects the signature
(consecutive finish=="length" steps with no tool call) and demotes the model
to the text-JSON path instead of looping to the budget.

Offline: `_stream_chat` is stubbed, so no network and no model.
"""

import sk.agent as agent
import sk.store as store
from sk.config import Config


def _cfg(model="silent-model"):
    return Config(
        provider="openrouter",
        model=model,
        base_url="http://x/v1",
        api_key="k",
        max_steps=6,
        temperature=0.0,
    )


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(agent, "_tools_unsupported", set())
    monkeypatch.setattr(agent, "_silent_tool_streak", {})


def test_streak_counts_only_cutoffs(monkeypatch):
    monkeypatch.setattr(agent, "_silent_tool_streak", {})
    assert agent._note_silent_tool_turn("m", True) == 1
    assert agent._note_silent_tool_turn("m", True) == 2
    assert agent._note_silent_tool_turn("m", False) == 0  # real answer resets
    assert agent._note_silent_tool_turn("m", True) == 1
    assert agent.SILENT_TOOLS_TRIPS_AT == 3


def test_streak_is_per_model(monkeypatch):
    monkeypatch.setattr(agent, "_silent_tool_streak", {})
    assert agent._note_silent_tool_turn("a", True) == 1
    assert agent._note_silent_tool_turn("b", True) == 1  # b unaffected by a
    assert agent._note_silent_tool_turn("a", True) == 2


def test_clear_resets_and_never_raises():
    agent._silent_tool_streak.clear()
    agent._note_silent_tool_turn("m", True)
    agent._clear_silent_tool_streak("m")
    assert agent._silent_tool_streak == {}
    agent._clear_silent_tool_streak("")  # must not raise
    agent._clear_silent_tool_streak("never-seen")


def test_silent_model_demoted_to_text_json(tmp_path, monkeypatch):
    """A model that never calls a tool is taken off the native path mid-turn."""
    _iso(tmp_path, monkeypatch)
    cfg = _cfg()
    seen = []

    def fake_stream(client, model, messages, tools, *a, **k):
        seen.append(tools is not None)
        # always cut off, never a tool call, never text-JSON
        return agent._Msg("I am thinking about it", None, "", "length")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    # native_tools_for lives in model_profiles and is imported into run_agent's
    # local scope, so patch it there; unprofiled models already default True.
    monkeypatch.setattr("sk.model_profiles.native_tools_for", lambda m: True)
    seen_notes: list[str] = []
    agent.run_agent(
        "do the thing",
        [],
        cfg,
        approve=lambda n, a: True,
        session="s",
        on_token=seen_notes.append,
    )
    # tools were dropped at some point (tools=None appears in the calls)
    assert seen and seen[-1] is False, f"model never demoted; tools sent every call: {seen}"
    # the user is told, rather than silently getting a recap
    assert any("never called one" in n for n in seen_notes), seen_notes
    assert cfg.model in agent._tools_unsupported


def test_tool_using_model_is_not_demoted(tmp_path, monkeypatch):
    """A model that calls tools normally must never trip the detector."""
    _iso(tmp_path, monkeypatch)
    cfg = _cfg("good-model")

    def fake_stream(client, model, messages, tools, *a, **k):
        tc = agent._TC("c1", "exec", '{"cmd": "echo hi"}')
        return agent._Msg("", [tc], "", "tool_calls")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    monkeypatch.setattr("sk.model_profiles.native_tools_for", lambda m: True)
    agent.run_agent("list files", [], cfg, approve=lambda n, a: True, session="s")
    assert "good-model" not in agent._tools_unsupported
