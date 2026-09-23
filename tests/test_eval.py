"""Eval harness: locks in every past quality failure as an offline regression test.

Each test mirrors a real bad answer we saw live:
- 3/10 generic LLM advice (no grounding) -> sysinfo + never-guess rules
- "~/neural-hangar does not exist" hallucination -> auto local facts
- "I can't fetch URLs" refusal -> auto web facts + never-refuse rule
- silent write execution -> approval gate
No Ollama needed: only prompt assembly + tools + gates are asserted.
"""

import sk.store as store
from sk.agent import SYSTEM_PROMPT, _gated_dispatch, build_messages
from sk.config import Config


def _cfg():
    return Config(model="t", base_url="http://x/v1", api_key="x", max_steps=1, temperature=0.0)


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


# --- prompt rules (the 3/10 generic-answer regression) ---


def test_prompt_forbids_cloud_models():
    assert "GPT-2" in SYSTEM_PROMPT and "Never recommend" in SYSTEM_PROMPT


def test_prompt_requires_sysinfo_grounding():
    assert "MUST call sysinfo" in SYSTEM_PROMPT and "Never guess RAM/GPU/CPU" in SYSTEM_PROMPT


def test_prompt_path_rule():
    assert "~/X means" in SYSTEM_PROMPT


def test_prompt_never_refuses_web():
    assert "never claim you cannot fetch" in SYSTEM_PROMPT.lower()


# --- message assembly ---


def test_build_includes_live_sysinfo(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    msgs = build_messages("what LLM can I run?", [], _cfg())
    system = msgs[0]["content"]
    assert "CPU:" in system and "OLLAMA MODELS" in system  # real local snapshot, not guessed


def test_build_local_facts_existing_dir(tmp_path, monkeypatch):
    import sk.agent as agent

    _iso(tmp_path, monkeypatch)
    (tmp_path / "sidekick").mkdir()
    (tmp_path / "sidekick" / "pyproject.toml").write_text("[project]\n")
    monkeypatch.setattr(agent.Path, "home", lambda: tmp_path)
    msgs = agent.build_messages("check ~/sidekick and tell me scope", [], _cfg())
    user = msgs[-1]["content"]
    assert "AUTO LOCAL FACTS" in user and "pyproject.toml" in user


def test_build_web_blocked_offline(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    msgs = build_messages("fetch http://127.0.0.1:9/ now", [], _cfg())
    user = msgs[-1]["content"]
    assert "AUTO WEB FACTS" in user and "blocked" in user.lower()


def test_build_no_facts_for_plain_chat(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    msgs = build_messages("say hi in 3 words", [], _cfg())
    assert "AUTO LOCAL FACTS" not in msgs[-1]["content"]
    assert "AUTO WEB FACTS" not in msgs[-1]["content"]


def test_build_injects_memory_and_todos(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    store.save_memory("prefers fast model for eval")
    store.add_todo("eval harness todo")
    msgs = build_messages("what do you remember about model?", [], _cfg())
    assert "prefers fast model for eval" in msgs[0]["content"]
    assert "eval harness todo" in msgs[0]["content"]


def test_history_trimmed_to_20(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    hist = [{"role": "user", "content": f"m{i}"} for i in range(50)]
    msgs = build_messages("hi", hist, _cfg())
    assert len(msgs) == 22  # system + 20 + user


# --- gates and schemas ---


def test_write_denied_without_approval():
    out, ok = _gated_dispatch("write_file", {"path": "/tmp/x", "content": "hi"}, approve=lambda n, a: False)
    assert ok is False and "--yes" in out


def test_read_tools_bypass_gate():
    out, ok = _gated_dispatch("list_dir", {"path": "/tmp"}, approve=lambda n, a: False)
    assert ok is True


def test_all_tools_parseable():
    from sk.agent import _parse_text_tool

    for name in ("sysinfo", "list_dir", "read_file", "exec", "shell", "delete_file", "write_file", "edit_file", "make_dir", "remember", "recall", "todo_add", "todo_list", "todo_done", "read_url", "web_search", "skill"):
        args = {"path": "/tmp/x"} if name in ("list_dir", "read_file") else {}
        import json

        blob = f'```json {json.dumps({"name": name, "arguments": args})} ```'
        assert _parse_text_tool(blob) is not None, name


def test_parse_multi_and_parameters_key():
    # exact shape of the live TUI failure: unfenced, several objects, "parameters" key
    from sk.agent import _parse_text_tools

    blob = '```\n{"name": "exec", "parameters": {"cmd": "df -h /"}}\n{"name": "read_url", "parameters": {"url": "https://example.com"}}\n{"name": "todo_done", "parameters": {"id": "1"}}\n```'
    hits = _parse_text_tools(blob)
    assert [n for n, _ in hits] == ["exec", "read_url", "todo_done"]
    assert hits[0][1] == {"cmd": "df -h /"}


def test_parse_openai_function_form():
    import json

    from sk.agent import _parse_text_tool

    blob = json.dumps({"function": {"name": "list_dir", "arguments": {"path": "."}}})
    assert _parse_text_tool(blob) == ("list_dir", {"path": "."})


def test_auto_search_triggers_and_ignores(monkeypatch):
    import sk.agent as agent

    monkeypatch.setattr("sk.tools.tool_web_search", lambda q, count=5: f"hits for {q}")
    out = agent._auto_search_context("search the internet for most used AI model")
    assert "most used AI model" in out and "hits for" in out
    assert agent._auto_search_context("say hi in 3 words") == ""


def test_auto_search_recency_not_local(monkeypatch):
    import sk.agent as agent

    seen = {}

    def fake_search(q, count=5):
        seen["q"] = q
        return f"hits for {q}"

    monkeypatch.setattr("sk.tools.tool_web_search", fake_search)
    out = agent._auto_search_context("what is the best laptop for a creative director right now")
    assert "AUTO" not in out and "hits for" in out  # search block, no refusal possible
    assert seen["q"] == "best laptop for creative director right now"  # keyword-compressed
    # local questions stay local even with recency words
    assert agent._auto_search_context("what LLM can I run right now") == ""
    assert agent._auto_search_context("my todos right now") == ""


def test_quick_reply_greetings():
    from sk.agent import _quick_reply, run_agent

    assert _quick_reply("hi") == "Hey! What are we working on?"
    assert _quick_reply("  Hello! ") is not None
    assert _quick_reply("thanks") == "Anytime!"
    assert _quick_reply("hi, check ~/x for scope") is None  # real task -> model
    assert _quick_reply("what is the disk usage") is None
    # instant + offline: no client needed
    assert run_agent("hi", [], _cfg()) == "Hey! What are we working on?"


def test_quick_reply_date():
    from datetime import datetime

    from sk.agent import _quick_reply, run_agent

    out = _quick_reply("what day is today")
    assert out is not None and datetime.now().strftime("%A") in out
    assert _quick_reply("what time is it") is not None
    assert _quick_reply("what day is the meeting") is None  # not a date question
    assert "20" in run_agent("what day is today", [], _cfg())  # instant, offline


def test_today_in_system_prompt(tmp_path, monkeypatch):
    import sk.store as store
    from datetime import datetime

    from sk.agent import build_messages

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    system = build_messages("hello", [], _cfg())[0]["content"]
    assert datetime.now().strftime("%Y-%m-%d") in system


def test_repeat_tool_uses_cache():
    from sk.agent import _run_tool_cached
    seen: dict[str, str] = {}
    calls: list[str] = []

    def fake_dispatch(name, args):
        calls.append(name)
        return "RESULT"

    import sk.agent as agent

    orig = agent.dispatch_tool
    agent.dispatch_tool = fake_dispatch  # type: ignore
    try:
        r1, rep1 = _run_tool_cached("read_url", {"url": "https://x", "max_chars": 400}, None, None, seen)
        r2, rep2 = _run_tool_cached("read_url", {"url": "https://x", "max_chars": 2000}, None, None, seen)
    finally:
        agent.dispatch_tool = orig
    assert (r1, rep1) == ("RESULT", False)
    assert rep2 is True and "already ran" in r2 and calls == ["read_url"]  # fetched once


def test_build_messages_auto_search(monkeypatch, tmp_path):
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr("sk.tools.tool_web_search", lambda q, count=5: "1. Example Model\n   https://example.com")
    from sk.agent import build_messages

    msgs = build_messages("search on the internet for the most used AI model", [], _cfg())
    assert "AUTO SEARCH" in msgs[-1]["content"] and "Example Model" in msgs[-1]["content"]


def test_prompt_greeting_and_search_rules():
    assert "GREETINGS" in SYSTEM_PROMPT and "direct one-line" in SYSTEM_PROMPT
    assert "web_search FIRST" in SYSTEM_PROMPT
    assert "SKILL INDEX" in SYSTEM_PROMPT and "call `skill`" in SYSTEM_PROMPT
    assert "don't ask in prose" in SYSTEM_PROMPT
    assert "Never narrate a denial you did not receive" in SYSTEM_PROMPT


def test_approval_mode_prompt(tmp_path, monkeypatch):
    import sk.store as store

    from sk.agent import build_messages

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    auto = build_messages("hi there", [], _cfg(), auto_approve=True)[0]["content"]
    assert "AUTOMATIC" in auto
    conf = build_messages("hi there", [], _cfg(), auto_approve=False)[0]["content"]
    assert "CONFIRM" in conf


def test_length_cut_continues_to_tools(monkeypatch, tmp_path):
    """A plan cut off by max_tokens (finish=length) must continue, not stop."""
    import sk.agent as agent
    import sk.store as store
    from sk.config import Config

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    cfg = Config(model="t", base_url="http://x/v1", api_key="x", max_steps=5, temperature=0.0)
    calls = {"n": 0}

    def fake_stream(client, model, messages, tools, temperature, max_tokens, extra, on_token=None, on_reasoning=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return agent._Msg("I will use write_file tool. Then", None, "", "length")
        if calls["n"] == 2:
            tc = agent._TC("c1", "exec", '{"cmd": "echo continued-ok"}')
            return agent._Msg("", [tc], "", "tool_calls")
        return agent._Msg("done continued-ok", None, "", "stop")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    out = agent.run_agent("do the thing", [], cfg, approve=lambda n, a: True)
    assert calls["n"] >= 2 and "continued-ok" in out
    # genuine stop still stops after exactly one model call
    calls["n"] = 0

    def stop_once(*a, **k):
        calls["n"] += 1
        return agent._Msg("all done", None, "", "stop")

    monkeypatch.setattr(agent, "_stream_chat", stop_once)
    assert agent.run_agent("summarize the logs", [], cfg) == "all done"
    assert calls["n"] == 1


def test_retryable_status():
    from sk.agent import _retryable_status

    assert _retryable_status(Exception("Error code: 429 ... Please retry in 5.94s")) == 5
    assert _retryable_status(Exception("RESOURCE_EXHAUSTED")) == 5
    assert _retryable_status(Exception("overloaded, try later")) == 5
    assert _retryable_status(Exception("retry in 300s")) == 30  # capped
    assert _retryable_status(Exception("Error code: 500 boom")) == 0
    assert _retryable_status(Exception("Connection error")) == 0


def test_create_retries_then_succeeds(monkeypatch):
    import sk.agent as agent

    calls = {"n": 0}
    notes: list[str] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise Exception("Error code: 429 ... Please retry in 0.1s")
            return "STREAM-OK"

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    import time as _t

    monkeypatch.setattr(_t, "sleep", lambda s: None)
    out = agent._create_with_retry(FakeClient(), {}, tries=3, on_token=notes.append)
    assert out == "STREAM-OK" and calls["n"] == 3
    assert any("retrying" in n for n in notes)


def test_create_gives_up(monkeypatch):
    import time as _t

    import sk.agent as agent

    calls = {"n": 0}

    class FakeCompletions:
        def create(self, **kwargs):
            calls["n"] += 1
            raise Exception("Error code: 429 busy")

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(_t, "sleep", lambda s: None)
    import pytest

    with pytest.raises(Exception, match="429"):
        agent._create_with_retry(FakeClient(), {}, tries=2)
    assert calls["n"] == 2


def test_create_no_retry_on_fatal():
    import sk.agent as agent

    calls = {"n": 0}

    class FakeCompletions:
        def create(self, **kwargs):
            calls["n"] += 1
            raise Exception("Error code: 401 bad key")

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    import pytest

    with pytest.raises(Exception, match="401"):
        agent._create_with_retry(FakeClient(), {})
    assert calls["n"] == 1


def test_tools_rejected_detection():
    from sk.agent import _tools_rejected

    assert _tools_rejected(Exception("Tool calling is not supported with this model"))
    assert _tools_rejected(Exception("ERROR: tools are not supported by model"))
    assert _tools_rejected(Exception("does not support function calling"))
    assert not _tools_rejected(Exception("401 bad key"))
    assert not _tools_rejected(Exception("connection reset"))


def test_tool_unsupported_falls_back_to_text_tools(monkeypatch, tmp_path):
    """Groq 400 'tool calling is not supported' must retry WITHOUT tools.

    The model then emits tools as ```json text blocks, which the existing
    text-JSON parser executes — no crash, no lost turn.
    """
    import sk.agent as agent
    import sk.store as store
    from sk.config import Config

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    agent._tools_unsupported.clear()
    cfg = Config(model="no-tools-model", base_url="http://x/v1", api_key="x", max_steps=5, temperature=0.0)
    calls = {"n": 0, "tools": [None]}

    def fake_stream(client, model, messages, tools, temperature, max_tokens, extra, on_token=None, on_reasoning=None):
        calls["n"] += 1
        calls["tools"].append(tools)
        if calls["n"] == 1:
            raise Exception("Tool calling is not supported with this model")
        if calls["n"] == 2:
            blob = '```json {"name": "exec", "arguments": {"cmd": "pwd"}} ```'
            return agent._Msg(blob, None, "", "stop")
        return agent._Msg("done via text-tools", None, "", "stop")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    out = agent.run_agent("list files", [], cfg, approve=lambda n, a: True)
    assert calls["tools"][1] is not None  # first attempt had tools attached
    assert calls["tools"][2] is None  # retry disabled tools
    assert calls["n"] == 3
    assert "done via text-tools" in out
    assert "no-tools-model" in agent._tools_unsupported


def test_tool_unsupported_cached_between_turns(monkeypatch, tmp_path):
    """Once a model is flagged tool-unsupported, later turns skip the failing
    call entirely (first stream attempt already uses tools=None)."""
    import sk.agent as agent
    import sk.store as store
    from sk.config import Config

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    agent._tools_unsupported.add("no-tools-model")
    cfg = Config(model="no-tools-model", base_url="http://x/v1", api_key="x", max_steps=5, temperature=0.0)
    seen = {"tools_arg": "unset"}

    def fake_stream(client, model, messages, tools, temperature, max_tokens, extra, on_token=None, on_reasoning=None):
        seen["tools_arg"] = tools
        return agent._Msg("ok", None, "", "stop")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    out = agent.run_agent("summarize the logs", [], cfg)
    assert seen["tools_arg"] is None
    assert out == "ok"
