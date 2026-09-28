"""Reasoning-leak hardening (#205): no trace as chat, show-don't-tell recap,
stale control-note hygiene. No network."""

from sk.agent import CONTROL_TAG, _drop_stale_control


def test_drop_stale_control_keeps_newest_and_data():
    tool_results = {
        "role": "user",
        "content": "[tool exec result]\nok\nAnswer the original question concisely.",
    }
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "user", "content": CONTROL_TAG + "Continue: emit the tool calls now."},
        tool_results,
        {"role": "user", "content": CONTROL_TAG + "Continue with your answer now."},
    ]
    _drop_stale_control(messages)
    assert messages == [
        {"role": "user", "content": "hello"},
        tool_results,
        {"role": "user", "content": CONTROL_TAG + "Continue with your answer now."},
    ]


def test_drop_stale_control_noop_and_never_raises():
    msgs: list = [{"role": "user", "content": "plain"}]
    _drop_stale_control(msgs)
    assert msgs == [{"role": "user", "content": "plain"}]
    _drop_stale_control([{"role": "user", "content": CONTROL_TAG + "only"}])
    _drop_stale_control(None)  # type: ignore[arg-type]
    _drop_stale_control("not-a-list")  # type: ignore[arg-type]


def test_reasoning_only_turn_never_posts_trace(tmp_path, monkeypatch):
    """A reasoning-only response continues the loop; the trace never surfaces."""
    import sk.agent as agent
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")

    class _Msg:
        def __init__(self, content="", tool_calls=None, reasoning="", finish="stop"):
            self.content = content
            self.tool_calls = tool_calls
            self.reasoning = reasoning
            self.finish = finish

    calls = {"n": 0}

    def fake_stream(client, model, messages, tools, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Msg(content="", reasoning="We need continue no prose. First make_dir.")
        return _Msg(content="Here is your plan draft.")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    from sk.config import Config

    cfg = Config(
        provider="ollama",
        model="qwen2.5-coder:7b",
        base_url="",
        api_key="",
        max_steps=5,
        temperature=0.2,
    )
    out = agent.run_agent("draft a plan", [], cfg, session="s")
    assert out == "Here is your plan draft."
    assert "We need continue" not in out
    assert calls["n"] == 2


def test_recap_uses_show_dont_tell_shape(tmp_path, monkeypatch):
    """Exhaustion synthesis asks for the literal 3-line shape."""
    import sk.agent as agent
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    seen = {"nones": 0}

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
            seen["nones"] += 1
            if seen["nones"] == 1:
                return _Msg(content="")  # peek finds nothing
            seen["recap"] = list(messages)[-1]["content"]
            return _Msg(content="Accomplished: x\nBlocked: y\nNext: z")
        return _Msg(content="", tool_calls=[_TC()])

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    from sk.config import Config

    cfg = Config(
        provider="ollama",
        model="qwen2.5-coder:7b",
        base_url="",
        api_key="",
        max_steps=1,
        temperature=0.2,
    )
    out = agent.run_agent("do things", [], cfg, approve=lambda n, a: True, session="s")
    assert out == "Accomplished: x\nBlocked: y\nNext: z"
    assert "Accomplished: <one line>" in seen["recap"]
    assert "Reply with ONLY these 3 lines" in seen["recap"]


def test_length_continue_notes_expire(tmp_path, monkeypatch):
    """Two length cutoffs then an answer: only the newest control note survives."""
    import sk.agent as agent
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    payloads = []

    class _Msg:
        def __init__(self, content="", tool_calls=None, reasoning="", finish="stop"):
            self.content = content
            self.tool_calls = tool_calls
            self.reasoning = reasoning
            self.finish = finish

    def fake_stream(client, model, messages, tools, *a, **k):
        payloads.append([dict(m) for m in messages])
        if len(payloads) <= 2:
            return _Msg(content="partial", finish="length")
        return _Msg(content="final answer")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    from sk.config import Config

    cfg = Config(
        provider="ollama",
        model="qwen2.5-coder:7b",
        base_url="",
        api_key="",
        max_steps=5,
        temperature=0.2,
    )
    assert agent.run_agent("go", [], cfg, session="s") == "final answer"
    last = payloads[-1]
    tagged = [
        m
        for m in last
        if m.get("role") == "user" and str(m.get("content", "")).lstrip().startswith(CONTROL_TAG)
    ]
    assert len(tagged) == 1
