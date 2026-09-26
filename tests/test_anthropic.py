"""Native Anthropic backend tests: translation, auth, full loop. No network."""

import sk.anthropic_backend as ab
import sk.store as store
from sk.config import Config, provider_tier


def _cfg(**kw):
    base = {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "base_url": "",
        "api_key": "k",
        "max_steps": 5,
        "temperature": 0.2,
    }
    base.update(kw)
    return Config(**base)


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


# --- translation ---


def test_tools_schema_conversion():
    schema = [
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "List",
                "parameters": {"type": "object"},
            },
        },
        {"type": "function", "function": {"name": "", "description": "skip me"}},
        {"type": "function", "function": {"name": "sysinfo", "description": "Info"}},
    ]
    out = ab.openai_tools_to_anthropic(schema)
    assert len(out) == 2
    assert out[0]["name"] == "list_dir" and "input_schema" in out[0]
    assert out[1]["input_schema"] == {"type": "object", "properties": {}}


def test_messages_conversion_roles_and_merge():
    msgs = [
        {"role": "system", "content": "sys1"},
        {"role": "system", "content": "sys2"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ack"},
        {"role": "assistant", "content": "more"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "t1", "function": {"name": "sysinfo", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "t1", "content": "CPU: x"},
    ]
    system, out = ab.openai_messages_to_anthropic(msgs)
    assert system == "sys1\n\nsys2"
    roles = [m["role"] for m in out]
    assert roles == ["user", "assistant", "user"]  # all consecutive assistants merged
    texts = [b.get("text", "") for b in out[1]["content"] if b.get("type") == "text"]
    assert "ack" in texts and "more" in texts
    uses = [b for b in out[1]["content"] if b.get("type") == "tool_use"]
    assert uses and uses[0]["name"] == "sysinfo" and uses[0]["input"] == {}
    res = [b for b in out[2]["content"] if b.get("type") == "tool_result"]
    assert res and res[0]["tool_use_id"] == "t1"


def test_messages_leading_assistant_dropped():
    msgs = [{"role": "assistant", "content": "stray"}, {"role": "user", "content": "hi"}]
    _, out = ab.openai_messages_to_anthropic(msgs)
    assert [m["role"] for m in out] == ["user"]


# --- auth ---


def test_fetch_models_anthropic(monkeypatch):
    import httpx

    import sk.auth as auth

    seen = {}

    def fake_get(url, headers=None, **k):
        seen.update({"url": url, "h": headers})

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [{"id": "claude-sonnet-5"}]}

        return R()

    monkeypatch.setattr(httpx, "get", fake_get)
    assert auth.fetch_models("anthropic", "https://api.anthropic.com", "k") == ["claude-sonnet-5"]
    assert seen["url"] == "https://api.anthropic.com/v1/models"
    assert seen["h"]["x-api-key"] == "k" and seen["h"]["anthropic-version"] == "2023-06-01"


def test_ping_anthropic(monkeypatch):
    import httpx

    import sk.auth as auth

    seen = {}

    def fake_post(url, headers=None, json=None, **k):
        seen.update({"url": url, "h": headers, "j": json})

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"content": [{"type": "text", "text": "hi"}]}

        return R()

    monkeypatch.setattr(httpx, "post", fake_post)
    ok, msg = auth.ping("anthropic", "https://api.anthropic.com", "k", "claude-sonnet-5")
    assert ok is True and msg == "hi"
    assert seen["url"].endswith("/v1/messages")
    assert seen["j"]["model"] == "claude-sonnet-5" and seen["j"]["max_tokens"] == 5


def test_ping_surfaces_api_error_body(monkeypatch):
    import httpx

    import sk.auth as auth

    body = (
        '{"type":"error","error":{"type":"invalid_request_error",'
        '"message":"Your credit balance is too low to access the Anthropic API."}}'
    )

    class FakeResp:
        status_code = 400
        text = body

        def json(self):
            import json as _json

            return _json.loads(self.text)

    class FakeErr(Exception):
        def __init__(self):
            super().__init__("Client error '400 Bad Request'")
            self.response = FakeResp()

    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(FakeErr()))
    ok, msg = auth.ping("anthropic", "https://api.anthropic.com", "k", "claude-sonnet-5")
    assert ok is False and "credit balance" in msg


def test_ping_garbage_error_falls_back(monkeypatch):
    import httpx

    import sk.auth as auth

    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    ok, msg = auth.ping("anthropic", "https://api.anthropic.com", "k", "claude-sonnet-5")
    assert ok is False and msg == "boom"


# --- full loop (fake wire) ---


def _resp(content, stop="end_turn"):
    return {"content": content, "stop_reason": stop}


def _stream_resp(content, stop="end_turn"):
    return ([dict(b) for b in content], stop)


def test_text_turn_and_audit(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)

    def fake_stream(base, key, payload, on_token=None, on_reasoning=None, **k):
        for chunk in ("hello ", "there"):  # streaming: per-delta callbacks
            if on_token is not None:
                on_token(chunk)
        return _stream_resp([{"type": "text", "text": "hello there"}])

    monkeypatch.setattr(ab, "_stream", fake_stream)
    seen_tokens = []
    out = ab.run_anthropic_agent(
        "say hi", [], _cfg(), on_token=seen_tokens.append, approve=lambda n, a: True, session="s"
    )
    assert out == "hello there" and seen_tokens == ["hello ", "there"]
    rows = store.list_tool_runs("s")
    assert any(r["tool"] == "llm_call" and r["provider"] == "anthropic" for r in rows)
    assert all(r["host"] == "api.anthropic.com" for r in rows)
    assert store.is_local_traffic("anthropic", "api.anthropic.com") is False


def test_tool_use_turn_dispatches(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake_stream(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _stream_resp(
                [{"type": "tool_use", "id": "tu1", "name": "list_dir", "input": {"path": "/tmp"}}],
                stop="tool_use",
            )
        return _stream_resp([{"type": "text", "text": "tmp has files"}])

    monkeypatch.setattr(ab, "_stream", fake_stream)
    out = ab.run_anthropic_agent("list tmp", [], _cfg(), approve=lambda n, a: True, session="s")
    assert out == "tmp has files"
    tools = [r["tool"] for r in store.list_tool_runs("s")]
    assert "list_dir" in tools and "llm_call" in tools


def test_denied_tool_logged(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)

    def fake_stream(*a, **k):
        return _stream_resp(
            [{"type": "tool_use", "id": "tu9", "name": "shell", "input": {"cmd": "echo hi"}}],
            stop="tool_use",
        )

    monkeypatch.setattr(ab, "_stream", fake_stream)
    out = ab.run_anthropic_agent(
        "run it", [], _cfg(max_steps=1), approve=lambda n, a: False, session="s"
    )
    assert out == "(max steps reached)"
    denied = [r for r in store.list_tool_runs("s") if r["approved"] == 0]
    assert len(denied) == 1 and denied[0]["tool"] == "shell"


def test_run_agent_branches_to_native(tmp_path, monkeypatch):
    import sk.agent as agent

    _iso(tmp_path, monkeypatch)
    hit = {}
    monkeypatch.setattr(
        ab, "_stream", lambda *a, **k: _stream_resp([{"type": "text", "text": "via-native"}])
    )
    out = agent.run_agent("hello there friend", [], _cfg(), session="s")
    assert out == "via-native"
    assert any(r["tool"] == "llm_call" and r["session"] == "s" for r in store.list_tool_runs("s"))
    assert hit == {}  # _stream patched at backend; OpenAI client never constructed


# --- config surface ---


def test_anthropic_preset_and_tiers():
    from sk.config import PRESETS

    assert PRESETS["anthropic"]["base_url"] == "https://api.anthropic.com"
    assert provider_tier("anthropic", "fast", "d") == "claude-haiku-4-5"
    assert provider_tier("anthropic", "smart", "d") == "claude-sonnet-5"
    assert _cfg().effective_base_url() == "https://api.anthropic.com"


# --- prompt caching ---


def test_cache_breakpoints_system_and_tools():
    sys_payload, tools = ab._cache_breakpoints(
        "sys text", [{"name": "a"}, {"name": "b", "input_schema": {}}]
    )
    assert isinstance(sys_payload, list)
    assert sys_payload[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in tools[0]
    assert tools[1]["cache_control"] == {"type": "ephemeral"}
    assert tools[1]["name"] == "b"  # definition untouched apart from marker


def test_cache_breakpoints_blank_system_and_no_tools():
    sys_payload, tools = ab._cache_breakpoints("   ", [])
    assert sys_payload == "" and tools == []


def test_loop_sends_cache_markers(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    payloads = []

    def fake_stream(base, key, payload, on_token=None, on_reasoning=None, **k):
        payloads.append(payload)
        return ([{"type": "text", "text": "cached hi"}], "end_turn")

    monkeypatch.setattr(ab, "_stream", fake_stream)
    out = ab.run_anthropic_agent("say hi", [], _cfg(), session="s")
    assert out == "cached hi"
    sent = payloads[0]
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["tools"][-1]["cache_control"] == {"type": "ephemeral"}


def test_cache_usage_block_ignored_gracefully(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)

    def fake_stream(base, key, payload, on_token=None, on_reasoning=None, **k):
        return ([{"type": "text", "text": "hi"}], "end_turn")

    monkeypatch.setattr(ab, "_stream", fake_stream)
    assert ab.run_anthropic_agent("say hi", [], _cfg(), session="s") == "hi"


# --- SSE streaming (#62) ---


def _sse_stream(lines, status=200):
    """Fake httpx.stream context manager yielding canned SSE lines."""
    import json as _json

    class FakeResp:
        status_code = status

        def read(self):
            return _json.dumps({"error": {"message": "bad key"}}).encode()

        def iter_lines(self):
            return iter(lines)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_stream(method, url, **k):
        assert method == "POST" and url.endswith("/v1/messages")
        return FakeResp()

    return fake_stream


def _ev(event, obj):
    import json as _json

    return [f"event: {event}", f"data: {_json.dumps(obj)}", ""]


def test_stream_text_and_thinking_deltas(monkeypatch):
    import httpx

    lines = [
        *_ev("message_start", {"type": "message_start", "message": {"id": "m1"}}),
        *_ev(
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
        ),
        *_ev(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "hmm "},
            },
        ),
        *_ev(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "ok"},
            },
        ),
        *_ev("content_block_stop", {"type": "content_block_stop", "index": 0}),
        *_ev(
            "content_block_start",
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
        ),
        *_ev(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "hi "},
            },
        ),
        *_ev(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "there"},
            },
        ),
        *_ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
        *_ev("message_stop", {"type": "message_stop"}),
    ]
    monkeypatch.setattr(httpx, "stream", _sse_stream(lines))
    tokens, thoughts = [], []
    blocks, stop = ab._stream(
        "https://api.anthropic.com",
        "k",
        {"model": "m", "messages": []},
        tokens.append,
        thoughts.append,
    )
    assert stop == "end_turn"
    assert tokens == ["hi ", "there"] and thoughts == ["hmm ", "ok"]
    assert {"type": "thinking", "thinking": "hmm ok"} in blocks
    assert {"type": "text", "text": "hi there"} in blocks


def test_stream_thinking_falls_back_to_on_token(monkeypatch):
    import httpx

    lines = [
        *_ev(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "t"},
            },
        ),
    ]
    monkeypatch.setattr(httpx, "stream", _sse_stream(lines))
    tokens = []
    ab._stream("https://x", "k", {}, tokens.append, None)
    assert tokens == ["t"]


def test_stream_tool_use_input_json_accumulates(monkeypatch):
    import httpx

    lines = [
        *_ev(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "tu1", "name": "list_dir"},
            },
        ),
        *_ev(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"path": "/t'},
            },
        ),
        *_ev(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": 'mp"}'},
            },
        ),
    ]
    monkeypatch.setattr(httpx, "stream", _sse_stream(lines))
    blocks, _ = ab._stream("https://x", "k", {}, None, None)
    assert blocks == [
        {"type": "tool_use", "id": "tu1", "name": "list_dir", "input": {"path": "/tmp"}}
    ]


def test_stream_http_error_raises(monkeypatch):
    import httpx

    import pytest

    monkeypatch.setattr(httpx, "stream", _sse_stream([], status=401))
    with pytest.raises(RuntimeError, match="HTTP 401"):
        ab._stream("https://x", "bad", {}, None, None)


def test_stream_error_event_raises(monkeypatch):
    import httpx

    import pytest

    lines = _ev("error", {"type": "error", "error": {"message": "overloaded"}})
    monkeypatch.setattr(httpx, "stream", _sse_stream(lines))
    with pytest.raises(RuntimeError, match="overloaded"):
        ab._stream("https://x", "k", {}, None, None)


def test_agent_falls_back_to_post_when_stream_fails(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("sse down")

    monkeypatch.setattr(ab, "_stream", _boom)
    monkeypatch.setattr(
        ab, "_post", lambda *a, **k: {"content": [{"type": "text", "text": "fallback hi"}]}
    )
    seen = []
    out = ab.run_anthropic_agent("hi", [], _cfg(), on_token=seen.append, session="s")
    assert out == "fallback hi" and seen == ["fallback hi"]


def test_agent_stream_error_surfaces_when_post_fails(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("sse down")

    def _post_boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(ab, "_stream", _boom)
    monkeypatch.setattr(ab, "_post", _post_boom)
    out = ab.run_anthropic_agent("hi", [], _cfg(), session="s")
    assert out.startswith("Error talking to anthropic")
