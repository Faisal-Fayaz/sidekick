"""Plan-review gate tests: trigger rules, approve-once, deny-closed, audit trail."""

import sk.agent as agent
import sk.store as store
from sk.agent import _maybe_review_plan, format_plan
from sk.config import Config


def _cfg(**kw):
    base = {
        "provider": "ollama",
        "model": "t",
        "base_url": "http://x/v1",
        "api_key": "x",
        "max_steps": 5,
        "temperature": 0.0,
    }
    base.update(kw)
    return Config(**base)


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


CALLS = [("exec", {"cmd": "ls"}), ("shell", {"cmd": "echo hi"})]


def test_format_plan():
    text = format_plan(CALLS)
    assert text == "1. exec -> ls\n2. shell -> echo hi"


def test_no_review_single_call():
    proceed, fn = _maybe_review_plan([("shell", {"cmd": "x"})], None, lambda p, c: True, False)
    assert proceed is True
    assert fn is None  # untouched passthrough, per-tool prompt still applies


def test_no_review_all_reads():
    asked = []
    proceed, fn = _maybe_review_plan(
        [("exec", {"cmd": "a"}), ("list_dir", {"path": "."})],
        None,
        lambda p, c: asked.append(True),
        False,
    )
    assert proceed is True and asked == [] and fn is None


def test_no_review_without_reviewer_or_auto():
    proceed, _ = _maybe_review_plan(CALLS, None, None, False)
    assert proceed is True
    proceed, _ = _maybe_review_plan(CALLS, None, lambda p, c: True, True)
    assert proceed is True


def test_approved_plan_skips_per_tool_prompts(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    prompts = []
    proceed, fn = _maybe_review_plan(
        CALLS,
        lambda n, a: prompts.append((n, a)) or True,
        lambda p, c: True,
        False,
        session="s",
        provider="ollama",
        host="localhost",
    )
    assert proceed is True
    assert fn("shell", {"cmd": "echo hi"}) is True
    assert fn("exec", {"cmd": "ls"}) is True
    assert prompts == []  # plan-listed targets never re-prompt
    assert fn("shell", {"cmd": "rm -rf /"}) is True  # falls through to real approver
    assert prompts == [("shell", {"cmd": "rm -rf /"})]


def test_denied_plan_logs_audit_and_executes_nothing(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    ran = []
    monkeypatch.setattr(agent, "dispatch_tool", lambda n, a: ran.append(n) or "ok")
    proceed, _ = _maybe_review_plan(
        CALLS,
        lambda n, a: True,
        lambda p, c: False,
        False,
        session="s",
        provider="ollama",
        host="localhost",
    )
    assert proceed is False and ran == []
    rows = store.list_tool_runs("s")
    assert len(rows) == 1 and rows[0]["tool"] == "plan" and rows[0]["approved"] == 0


def test_reviewer_crash_fails_closed(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)

    def boom(plan, calls):
        raise RuntimeError("ui gone")

    proceed, _ = _maybe_review_plan(CALLS, None, boom, False, session="s")
    assert proceed is False
    assert store.list_tool_runs("s")[0]["tool"] == "plan"


def test_run_agent_plan_approved_end_to_end(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    import json

    calls = {"n": 0}

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
        calls["n"] += 1
        if calls["n"] > 1:
            return agent._Msg("all done", None, "", "stop")
        tcs = [
            agent._TC("c1", "exec", json.dumps({"cmd": "ls"})),
            agent._TC("c2", "shell", json.dumps({"cmd": "echo hi"})),
        ]
        return agent._Msg("", tcs, "", "tool_calls")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    monkeypatch.setattr(agent, "dispatch_tool", lambda n, a: f"ok-{n}")
    prompts, reviews = [], []
    out = agent.run_agent(
        "do it",
        [],
        _cfg(),
        approve=lambda n, a: prompts.append(n) or True,
        review_plan=lambda p, c: reviews.append(p) or True,
        session="s",
    )
    assert len(reviews) == 1 and "shell -> echo hi" in reviews[0]
    assert prompts == []  # plan approval covered both tools
    assert out == "all done"
    rows = store.list_tool_runs("s")
    assert {"exec", "shell"} <= {r["tool"] for r in rows}
    assert all(r["approved"] == 1 for r in rows if r["tool"] in ("exec", "shell"))


def test_run_agent_plan_denied_runs_nothing(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    import json

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
        tcs = [agent._TC("c1", "shell", json.dumps({"cmd": "echo hi"}))]
        tcs.append(agent._TC("c2", "exec", json.dumps({"cmd": "ls"})))
        return agent._Msg("", tcs, "", "tool_calls")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    ran = []
    monkeypatch.setattr(agent, "dispatch_tool", lambda n, a: ran.append(n) or "ok")
    out = agent.run_agent(
        "do it",
        [],
        _cfg(),
        approve=lambda n, a: True,
        review_plan=lambda p, c: False,
        session="s",
    )
    assert out == "Plan denied by user — nothing was executed."
    assert ran == []


def test_cli_reviewer_yolo_and_prompt(monkeypatch, capsys):
    from sk.cli.approvers import _make_plan_reviewer

    assert _make_plan_reviewer({"yolo": True})("plan", CALLS) is True
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    assert _make_plan_reviewer({"yolo": False})("1. shell -> x", CALLS) is True
    assert "plan" in capsys.readouterr().out
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
    assert _make_plan_reviewer({"yolo": False})("1. shell -> x", CALLS) is False


def test_tui_review_plan_sets_turn_scope(monkeypatch):
    from sk.tui.app import SidekickTUI

    app = SidekickTUI()
    monkeypatch.setattr(app, "_wait_slot", lambda *a, **k: True)
    assert app._review_plan("1. shell -> x", [("shell", {"cmd": "x"})]) is True
    assert app._plan_approved == {"shell|cmd=x"}
    monkeypatch.setattr(app, "_wait_slot", lambda *a, **k: False)
    assert app._review_plan("1. shell -> x", [("shell", {"cmd": "x"})]) is False
    assert app._plan_approved is None
