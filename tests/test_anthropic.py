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


# --- full loop (fake wire) ---


def _resp(content, stop="end_turn"):
    return {"content": content, "stop_reason": stop}


def test_text_turn_and_audit(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ab, "_post", lambda *a, **k: _resp([{"type": "text", "text": "hello there"}])
    )
    seen_tokens = []
    out = ab.run_anthropic_agent(
        "say hi", [], _cfg(), on_token=seen_tokens.append, approve=lambda n, a: True, session="s"
    )
    assert out == "hello there" and seen_tokens == ["hello there"]
    rows = store.list_tool_runs("s")
    assert any(r["tool"] == "llm_call" and r["provider"] == "anthropic" for r in rows)
    assert all(r["host"] == "api.anthropic.com" for r in rows)
    assert store.is_local_traffic("anthropic", "api.anthropic.com") is False


def test_tool_use_turn_dispatches(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp(
                [{"type": "tool_use", "id": "tu1", "name": "list_dir", "input": {"path": "/tmp"}}],
                stop="tool_use",
            )
        return _resp([{"type": "text", "text": "tmp has files"}])

    monkeypatch.setattr(ab, "_post", fake_post)
    out = ab.run_anthropic_agent("list tmp", [], _cfg(), approve=lambda n, a: True, session="s")
    assert out == "tmp has files"
    tools = [r["tool"] for r in store.list_tool_runs("s")]
    assert "list_dir" in tools and "llm_call" in tools


def test_denied_tool_logged(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)

    def fake_post(*a, **k):
        return _resp(
            [{"type": "tool_use", "id": "tu9", "name": "shell", "input": {"cmd": "echo hi"}}],
            stop="tool_use",
        )

    monkeypatch.setattr(ab, "_post", fake_post)
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
        ab, "_post", lambda *a, **k: _resp([{"type": "text", "text": "via-native"}])
    )
    out = agent.run_agent("hello there friend", [], _cfg(), session="s")
    assert out == "via-native"
    assert any(r["tool"] == "llm_call" and r["session"] == "s" for r in store.list_tool_runs("s"))
    assert hit == {}  # _post patched at backend; OpenAI client never constructed


# --- config surface ---


def test_anthropic_preset_and_tiers():
    from sk.config import PRESETS

    assert PRESETS["anthropic"]["base_url"] == "https://api.anthropic.com"
    assert provider_tier("anthropic", "fast", "d") == "claude-haiku-4-5"
    assert provider_tier("anthropic", "smart", "d") == "claude-sonnet-5"
    assert _cfg().effective_base_url() == "https://api.anthropic.com"
