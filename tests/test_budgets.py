"""Adaptive budgets (#198): profile rows, precedence, exhaustion backstop. No network."""

from sk.config import Config
from sk.model_profiles import (
    effective_max_steps,
    max_steps_for,
    max_tokens_for,
)


def _cfg(**kw):
    base = {
        "provider": "ollama",
        "model": "qwen2.5-coder:7b",
        "base_url": "",
        "api_key": "",
        "max_steps": 5,
        "temperature": 0.2,
    }
    base.update(kw)
    return Config(**base)


def test_local_rows_lock_current_constants():
    assert (max_tokens_for("llama3.2:3b", "ollama"), max_steps_for("llama3.2:3b")) == (350, 5)
    assert (max_tokens_for("qwen2.5-coder:7b", "ollama"), max_steps_for("qwen2.5-coder:7b")) == (
        350,
        5,
    )


def test_frontier_rows():
    assert (max_tokens_for("gpt-4o", "openai"), max_steps_for("gpt-4o")) == (2000, 15)
    assert (max_tokens_for("claude-sonnet-5", "anthropic"), max_steps_for("claude-sonnet-5")) == (
        2000,
        15,
    )
    assert (max_tokens_for("deepseek-chat", "deepseek"), max_steps_for("deepseek-chat")) == (
        2000,
        15,
    )


def test_unknown_model_falls_back():
    assert max_tokens_for("mystery-model", "ollama") == 350
    assert max_tokens_for("mystery-model", "openai") == 800
    assert max_steps_for("mystery-model") is None


def test_precedence_config_profile_default():
    frontier = _cfg(provider="openai", model="gpt-4o")
    assert effective_max_steps("gpt-4o", frontier) == 15  # profile (config at default)
    custom = _cfg(provider="openai", model="gpt-4o", max_steps=3)
    custom.max_steps_custom = True
    assert effective_max_steps("gpt-4o", custom) == 3  # explicit config wins
    plain = _cfg(provider="openai", model="mystery-model")
    assert effective_max_steps("mystery-model", plain) == 5  # configured default
    local = _cfg()
    assert effective_max_steps("qwen2.5-coder:7b", local) == 5


def test_config_provenance(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    assert Config.load().max_steps_custom is False
    _cfg().save()  # auto-saved default must not count as customization
    assert Config.load().max_steps_custom is False
    # legacy auto-saved 5 follows the profile: frontier budgets apply
    assert effective_max_steps("gpt-4o", Config.load()) == 15
    cfg = Config.load()
    cfg.max_steps = 9
    cfg.save()
    loaded = Config.load()
    assert loaded.max_steps == 9 and loaded.max_steps_custom is True
    assert effective_max_steps("gpt-4o", loaded) == 9  # explicit config wins


def test_exhaustion_synthesizes_openai(tmp_path, monkeypatch):
    """5 tool turns + empty peek -> bounded recap call, never the bare sentinel."""
    import sk.agent as agent
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    calls = {"n": 0}

    class _Msg:
        def __init__(self, content="", tool_calls=None, reasoning="", finish="stop"):
            self.content = content
            self.tool_calls = tool_calls
            self.reasoning = reasoning
            self.finish = finish

    class _TC:
        def __init__(self):
            self.id = "t1"
            self.function = type("F", (), {"name": "list_dir", "arguments": '{"path": "."}'})()

    def fake_stream(client, model, messages, tools, *a, **k):
        calls["n"] += 1
        if tools is None:
            # peek (1st) is empty -> loop ends -> recap (2nd) reports
            return _Msg(
                content="" if calls["n"] <= 6 else "wrapped: listed dir, blocked on nothing"
            )
        return _Msg(content="", tool_calls=[_TC()])

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    cfg = _cfg()  # local qwen: 5 steps
    out = agent.run_agent("do things", [], cfg, approve=lambda n, a: True, session="s")
    assert out == "wrapped: listed dir, blocked on nothing"
    assert calls["n"] == 7  # 5 tool turns + empty peek + recap


def test_exhaustion_fallback_sentinel_openai(tmp_path, monkeypatch):
    import sk.agent as agent
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")

    class _Msg:
        def __init__(self, content="", tool_calls=None, reasoning="", finish="stop"):
            self.content = content
            self.tool_calls = tool_calls
            self.reasoning = reasoning
            self.finish = finish

    class _TC:
        def __init__(self):
            self.id = "t1"
            self.function = type("F", (), {"name": "list_dir", "arguments": '{"path": "."}'})()

    def fake_stream(client, model, messages, tools, *a, **k):
        if tools is None:
            raise RuntimeError("recap down")
        return _Msg(content="", tool_calls=[_TC()])

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    out = agent.run_agent("do things", [], _cfg(), approve=lambda n, a: True, session="s")
    assert out == "(max steps reached)"


def test_exhaustion_synthesizes_anthropic(tmp_path, monkeypatch):
    import sk.anthropic_backend as ab
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(
        ab,
        "_stream",
        lambda *a, **k: (
            [{"type": "tool_use", "id": "t1", "name": "list_dir", "input": {"path": "/tmp"}}],
            "tool_use",
        ),
    )
    seen = {}

    def fake_post(base, key, payload, **k):
        seen["payload"] = payload
        return {"content": [{"type": "text", "text": "recap: listed tmp, nothing blocked"}]}

    monkeypatch.setattr(ab, "_post", fake_post)
    cfg = _cfg(provider="anthropic", model="claude-haiku-4-5", max_steps=2)
    cfg.max_steps_custom = True  # keep the loop short: 2 tool turns, then recap
    out = ab.run_anthropic_agent("do things", [], cfg, approve=lambda n, a: True, session="s")
    assert out == "recap: listed tmp, nothing blocked"
    assert "tools" not in seen["payload"]  # recap is a no-tools call


def test_error_carries_progress_openai(tmp_path, monkeypatch):
    """#235: mocked 400 mid-turn after writes returns progress, cause, hint."""
    import sk.agent as agent
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    calls = {"n": 0}

    class _Msg:
        def __init__(self, content="", tool_calls=None, reasoning="", finish="stop"):
            self.content = content
            self.tool_calls = tool_calls
            self.reasoning = reasoning
            self.finish = finish

    class _TC:
        def __init__(self, name="list_dir", args='{"path": "."}'):
            self.id = "t1"
            self.function = type("F", (), {"name": name, "arguments": args})()

    def fake_stream(client, model, messages, tools, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Msg(content="", tool_calls=[_TC()])
        raise RuntimeError("Error code: 400 - tool_use_failed")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    out = agent.run_agent("do things", [], _cfg(), approve=lambda n, a: True, session="s")
    assert out.startswith("Turn failed partway")
    assert "list_dir" in out and "400" in out and "Resume with" in out


def test_error_without_progress_still_raises(tmp_path, monkeypatch):
    """Zero completed work preserves the old raise contract."""
    import sk.agent as agent
    import sk.store as store

    import pytest

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")

    def fake_stream(*a, **k):
        raise RuntimeError("Error code: 500 - boom")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    with pytest.raises(RuntimeError, match="500"):
        agent.run_agent("do things", [], _cfg(), session="s")
