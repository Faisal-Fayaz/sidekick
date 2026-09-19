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
    _iso(tmp_path, monkeypatch)
    msgs = build_messages("check ~/sidekick and tell me scope", [], _cfg())
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

    for name in ("sysinfo", "list_dir", "read_file", "exec", "write_file", "edit_file", "remember", "recall", "todo_add", "todo_list", "todo_done", "read_url", "web_search", "skill"):
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
