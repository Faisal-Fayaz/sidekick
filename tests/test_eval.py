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

    for name in ("sysinfo", "list_dir", "read_file", "exec", "write_file", "edit_file", "remember", "recall", "todo_add", "todo_list", "todo_done", "read_url"):
        args = {"path": "/tmp/x"} if name in ("list_dir", "read_file") else {}
        import json

        blob = f'```json {json.dumps({"name": name, "arguments": args})} ```'
        assert _parse_text_tool(blob) is not None, name
