"""Capability profile tests: matching, routing, protocol seeding, parallel width."""

import sk.agent as agent
from sk.config import Config
from sk.model_profiles import DEFAULT_PROFILE, PROFILES, match_profile
from sk.router import FAST_MODEL, SMART_MODEL, route


def _cfg(**kw):
    base = {
        "provider": "ollama",
        "model": "t",
        "base_url": "http://x/v1",
        "api_key": "x",
        "max_steps": 2,
        "temperature": 0.0,
    }
    base.update(kw)
    return Config(**base)


def test_match_table():
    assert match_profile("llama3.2:3b")["native_tools"] is False
    assert match_profile("LLAMA3.2:3B")["native_tools"] is False
    assert match_profile("qwen2.5-coder:7b")["native_tools"] is True
    assert match_profile("qwen2.5-coder:7b")["max_parallel"] == 4
    assert match_profile("llama3.2:3b")["max_parallel"] == 2
    assert match_profile("some-future-model:99b") == DEFAULT_PROFILE
    assert match_profile("") == DEFAULT_PROFILE
    assert set(PROFILES) >= {"llama3.2:3b", "qwen2.5-coder:7b"}
    # returned dicts are copies: mutating never pollutes the table
    match_profile("llama3.2:3b")["native_tools"] = True
    assert match_profile("llama3.2:3b")["native_tools"] is False


def test_route_consumes_profiles():
    r = route("refactor this function")
    assert r["tier"] == "smart" and r["model"] == SMART_MODEL
    assert r["native_tools"] is True and r["max_parallel"] == 4
    r = route("refactor this function", model_override="llama3.2:3b")
    assert r["model"] == "llama3.2:3b" and r["native_tools"] is False
    assert r["max_parallel"] == 2 and "override" in r["reason"]
    r = route("say hi")
    assert r["tier"] == "fast" and r["model"] == FAST_MODEL
    r = route("anything", provider="groq")
    assert r["model"]  # tier-mapped, profile looked up (default if unverified)


def test_text_only_model_skips_native_first_try(monkeypatch):
    seen = {}

    def fake_stream(
        client,
        model,
        messages,
        tools,
        temperature,
        max_tokens,
        extra,
        on_token=None,
        on_reasoning=None,
    ):
        seen.setdefault("first_tools", tools)
        return agent._Msg("done", None, "", "stop")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    out = agent.run_agent("hi there friend", [], _cfg(model="llama3.2:3b"), session="s")
    assert out == "done"
    assert seen["first_tools"] is None  # no wasted native attempt


def test_native_model_sends_schema_first_try(monkeypatch):
    seen = {}

    def fake_stream(
        client,
        model,
        messages,
        tools,
        temperature,
        max_tokens,
        extra,
        on_token=None,
        on_reasoning=None,
    ):
        seen.setdefault("first_tools", tools)
        seen.setdefault("messages", messages)
        return agent._Msg("done", None, "", "stop")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    agent.run_agent("hi there friend", [], _cfg(model="qwen2.5-coder:7b"), session="s")
    assert seen["first_tools"] is not None  # schema offered immediately


def test_seed_note_present_for_text_only(monkeypatch):
    seen = {}

    def fake_stream(
        client,
        model,
        messages,
        tools,
        temperature,
        max_tokens,
        extra,
        on_token=None,
        on_reasoning=None,
    ):
        seen.setdefault("messages", [dict(m, content=str(m.get("content", ""))) for m in messages])
        return agent._Msg("done", None, "", "stop")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    agent.run_agent("hi there friend", [], _cfg(model="llama3.2:3b"), session="s")
    blob = "\n".join(m["content"] for m in seen["messages"])
    assert "does not support native tool calling" in blob


def test_runtime_rejection_still_wins(monkeypatch):
    agent._tools_unsupported.add("qwen2.5-coder:7b")
    try:
        seen = {}

        def fake_stream(
            client,
            model,
            messages,
            tools,
            temperature,
            max_tokens,
            extra,
            on_token=None,
            on_reasoning=None,
        ):
            seen.setdefault("first_tools", tools)
            return agent._Msg("done", None, "", "stop")

        monkeypatch.setattr(agent, "_stream_chat", fake_stream)
        agent.run_agent("hi there friend", [], _cfg(model="qwen2.5-coder:7b"), session="s")
        assert seen["first_tools"] is None  # runtime learning beats the profile
    finally:
        agent._tools_unsupported.discard("qwen2.5-coder:7b")


def test_parallel_width_follows_profile(monkeypatch):
    import threading
    import time

    import sk.agent as _agent

    state = {"depth": 0, "max_depth": 0, "lock": threading.Lock()}

    def fake_dispatch(name, args):
        with state["lock"]:
            state["depth"] += 1
            state["max_depth"] = max(state["max_depth"], state["depth"])
        try:
            time.sleep(0.05)
            return f"ok-{name}"
        finally:
            with state["lock"]:
                state["depth"] -= 1

    monkeypatch.setattr(_agent, "dispatch_tool", fake_dispatch)

    def fake_stream(
        client,
        model,
        messages,
        tools,
        temperature,
        max_tokens,
        extra,
        on_token=None,
        on_reasoning=None,
    ):
        if getattr(fake_stream, "done", False):
            return _agent._Msg("all done", None, "", "stop")
        fake_stream.done = True
        tcs = [
            _agent._TC(f"c{i}", name, "{}")
            for i, name in enumerate(["exec", "list_dir", "sysinfo", "recall"])
        ]
        return _agent._Msg("", tcs, "", "tool_calls")

    fake_stream.done = False
    monkeypatch.setattr(_agent, "_stream_chat", fake_stream)
    out = _agent.run_agent(
        "gather everything",
        [],
        _cfg(model="llama3.2:3b", max_steps=3),
        approve=None,
        session="s",
    )
    assert out == "all done"
    assert state["max_depth"] <= 2  # llama profile caps the pool deterministically
