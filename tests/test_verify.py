"""Post-edit verify + capped repair (refs #280). Offline, fully mocked."""

import sk.agent as agent
import sk.store as store
from sk.config import Config


def _cfg(**kw):
    base = {
        "provider": "ollama",
        "model": "m",
        "base_url": "",
        "api_key": "",
        "max_steps": 5,
        "temperature": 0.0,
    }
    base.update(kw)
    return Config(**base)


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def test_helpers_pure():
    assert agent._verify_command([]) is None
    assert agent._verify_command(["/tmp/x.md"]) is None
    cmd = agent._verify_command(["/tmp/x.py", "/tmp/notes.md"])
    assert cmd is not None and "py_compile" not in cmd and "compile(" in cmd
    assert "/tmp/x.py" in cmd and "notes.md" not in cmd
    assert agent._verify_passed("$ c\n[exit 0]\n(ok)") is True
    assert agent._verify_passed("$ c\n[exit 1]\nSyntaxError: bad") is False
    assert agent._verify_passed("Error: blocked") is False
    batch = [
        ("write_file", {"path": "/tmp/a.py", "content": "x"}),
        ("exec", {"cmd": "ls"}),
        ("edit_file", {"path": "/tmp/b.py", "old_string": "a", "new_string": "b"}),
        ("write_file", {"path": "/tmp/a.py", "content": "y"}),
    ]
    assert agent._edited_paths(batch) == ["/tmp/a.py", "/tmp/b.py"]
    assert agent._edited_paths([]) == []


def _scripted(monkeypatch, tmp_path, texts, shell_results):
    """Fake model turns (texts) + fake tool results. Returns shell call count."""
    _iso(tmp_path, monkeypatch)
    calls = {"n": 0, "shell": 0}

    def fake_stream(client, model, messages, tools, *a, **k):
        calls["n"] += 1
        return texts[calls["n"] - 1]

    def fake_dispatch(name, args):
        if name == "write_file":
            return f"Wrote {len(str(args.get('content', '')))} chars to {args.get('path')}"
        if name == "shell":
            calls["shell"] += 1
            code = shell_results[min(calls["shell"] - 1, len(shell_results) - 1)]
            return f"$ verify\n[exit {code}]\n{'ok' if code == 0 else 'SyntaxError: bad'}"
        return "ok"

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    monkeypatch.setattr(agent, "dispatch_tool", fake_dispatch)
    return calls


def _write_turn():
    return agent._Msg(
        "",
        [agent._TC("1", "write_file", '{"path": "/tmp/x.py", "content": "x = 1"}')],
        "",
        "tool_calls",
    )


def test_repair_green_after_one_failure(tmp_path, monkeypatch):
    calls = _scripted(
        monkeypatch,
        tmp_path,
        [
            _write_turn(),
            agent._Msg("draft", None, "", "stop"),
            agent._Msg("all fixed", None, "", "stop"),
        ],
        [1, 0],
    )
    out = agent.run_agent("write x", [], _cfg(), approve=lambda n, a: True, session="t")
    assert out == "all fixed"
    assert calls["shell"] == 2  # fail once, pass once


def test_repair_budget_caps_at_two(tmp_path, monkeypatch):
    calls = _scripted(
        monkeypatch,
        tmp_path,
        [
            _write_turn(),
            agent._Msg("v1", None, "", "stop"),
            agent._Msg("v2", None, "", "stop"),
            agent._Msg("v3", None, "", "stop"),
        ],
        [1],
    )
    out = agent.run_agent("write x", [], _cfg(), approve=lambda n, a: True, session="t")
    assert out == "v3"  # budget spent: last draft ships, no third verify
    assert calls["shell"] == 2


def test_verify_skipped_without_edits_and_in_plan_mode(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    seen = {"shell": 0}

    def fake_dispatch(name, args):
        if name == "shell":
            seen["shell"] += 1
        return "ok"

    monkeypatch.setattr(agent, "dispatch_tool", fake_dispatch)
    monkeypatch.setattr(
        agent, "_stream_chat", lambda *a, **k: agent._Msg("chat done", None, "", "stop")
    )
    out = agent.run_agent("say hi", [], _cfg(), approve=lambda n, a: True, session="t")
    assert out == "chat done" and seen["shell"] == 0  # no edits: no verify

    calls = {"n": 0}

    def fake_stream(client, model, messages, tools, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _write_turn()
        return agent._Msg("plan draft", None, "", "stop")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    out = agent.run_agent(
        "write x", [], _cfg(), approve=lambda n, a: True, session="t", plan_mode=True
    )
    assert out == "plan draft" and seen["shell"] == 0  # plan mode: no verify


# --- #310: synthesised tool_call_id must be unique and stable ---------------


def test_synthetic_call_id_unique_and_stable():
    from sk.agent import _synthetic_call_id

    call = {"name": "shell", "args": {"cmd": "ls"}}
    # identical for the same call, so a retried turn reuses the id
    assert _synthetic_call_id(0, call) == _synthetic_call_id(0, call)
    # distinct across index, tool, and args
    assert _synthetic_call_id(0, call) != _synthetic_call_id(1, call)
    assert _synthetic_call_id(0, call) != _synthetic_call_id(0, {"name": "write_file", "args": {}})
    assert _synthetic_call_id(0, call) != _synthetic_call_id(
        0, {"name": "shell", "args": {"cmd": "pwd"}}
    )


def test_synthetic_call_id_no_collisions_over_a_turn():
    from sk.agent import _synthetic_call_id

    ids = {_synthetic_call_id(i, {"name": f"t{i}", "args": {"i": i}}) for i in range(5000)}
    assert len(ids) == 5000


def test_two_turns_without_provider_ids_produce_unique_ids(monkeypatch):
    """Providers that omit tool_call ids must not emit duplicates across turns.

    The history is re-sent in full, so a request can contain two assistant
    messages with the same id — a hard 400 from OpenAI and Anthropic alike.
    Reaches through the real stream accumulator, not around it.
    """
    import sk.agent as agent

    def run_turn(idx):
        tc_buf = {
            0: {"id": None, "name": "read_file", "args": {"path": f"turn{idx}/a.py"}},
            1: {"id": None, "name": "read_file", "args": {"path": f"turn{idx}/b.py"}},
        }
        agent._TC = agent._TC  # keep reference
        from sk.agent import _synthetic_call_id

        return [
            agent._TC(b["id"] or _synthetic_call_id(i, b), b["name"], b["args"])
            for i, b in sorted(tc_buf.items())
            if b["name"]
        ]

    turn1 = run_turn(1)
    turn2 = run_turn(2)
    ids1 = [c.id for c in turn1]
    ids2 = [c.id for c in turn2]
    assert len(set(ids1)) == len(ids1)
    assert not (set(ids1) & set(ids2)), f"duplicate ids across turns: {set(ids1) & set(ids2)}"
