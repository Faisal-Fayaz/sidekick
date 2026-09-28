"""Build/plan mode tests (fixes #131): file-write denial with shell still
asking, prompt lines on both backends, slash toggles, run --plan, TUI gate.
Fully offline."""

import sk.slash as slash
from sk.agent import build_messages
from sk.cli.approvers import (
    PLAN_DENIED_TOOLS,
    _make_approver,
    _make_approver_state,
    _make_plan_reviewer,
)
from sk.config import Config

WRITES = ["write_file", "edit_file", "delete_file", "make_dir"]
READS = ["exec", "read_file", "list_dir", "sysinfo"]


def _cfg():
    return Config(
        provider="ollama",
        model="t",
        base_url="http://x/v1",
        api_key="x",
        max_steps=1,
        temperature=0.0,
    )


def _sctx(tmp_path, monkeypatch):
    import sk.config as config_mod
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    return {
        "session": "test",
        "cfg": _cfg(),
        "state": {"yolo": False, "readonly": False, "plan": False},
    }


def test_plan_denied_tools_cover_writes():
    assert set(PLAN_DENIED_TOOLS) == set(WRITES) | {"generate_image"}


def test_one_shot_approver_plan_denies_writes_shell_asks(monkeypatch):
    import typer

    monkeypatch.setattr(typer, "confirm", lambda *a, **k: True)
    approve = _make_approver(False, (), (), plan_mode=True)
    for tool in WRITES:
        assert approve(tool, {"path": "x"}) is False
    assert approve("shell", {"cmd": "git log"}) is True  # exploration still asks
    for tool in READS:
        assert approve(tool, {"path": "x", "cmd": "ls"}) is True


def test_state_approver_plan_beats_yolo(monkeypatch):
    import typer

    monkeypatch.setattr(typer, "confirm", lambda *a, **k: True)
    approve = _make_approver_state({"yolo": True, "plan": True}, (), ())
    assert approve("write_file", {"path": "x"}) is False
    assert approve("shell", {"cmd": "ls"}) is True  # yolo still auto-passes shell
    assert approve("exec", {"cmd": "ls"}) is True


def test_plan_reviewer_denies_write_plans_allows_shell_plans(monkeypatch):
    import typer

    monkeypatch.setattr(typer, "confirm", lambda *a, **k: True)
    review = _make_plan_reviewer({"plan": True})
    assert review("plan", [("write_file", {"path": "x"})]) is False
    assert review("plan", [("shell", {"cmd": "git log"})]) is True
    assert review("plan", [("exec", {"cmd": "ls"})]) is True


def test_slash_plan_build_toggles(tmp_path, monkeypatch):
    c = _sctx(tmp_path, monkeypatch)
    out = slash.handle("/plan", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled is True
    assert c["state"] == {"yolo": False, "readonly": False, "plan": True}
    out = slash.handle("/build", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled is True
    assert c["state"] == {"yolo": False, "readonly": False, "plan": False}
    slash.handle("/yolo", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert c["state"]["plan"] is False
    slash.handle("/plan", session=c["session"], cfg=c["cfg"], state=c["state"])
    slash.handle("/readonly", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert c["state"] == {"yolo": False, "readonly": True, "plan": False}
    slash.handle("/plan", session=c["session"], cfg=c["cfg"], state=c["state"])
    slash.handle("/confirm", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert c["state"] == {"yolo": False, "readonly": False, "plan": False}


def test_help_lists_plan_build(tmp_path, monkeypatch):
    c = _sctx(tmp_path, monkeypatch)
    out = slash.handle("/help", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert "/plan" in out.text and "/build" in out.text


def test_build_messages_plan_line(tmp_path, monkeypatch):
    _sctx(tmp_path, monkeypatch)
    sys_plan = build_messages("hi", [], _cfg(), plan_mode=True)[0]["content"]
    assert "Approval mode: PLAN" in sys_plan
    sys_conf = build_messages("hi", [], _cfg())[0]["content"]
    assert "Approval mode: PLAN" not in sys_conf
    sys_ro = build_messages("hi", [], _cfg(), read_only=True, plan_mode=True)[0]["content"]
    assert "Approval mode: READ-ONLY" in sys_ro  # readonly wins


def test_anthropic_plan_line(tmp_path, monkeypatch):
    import json as _json

    import sk.anthropic_backend as ab
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    payloads = []

    def fake_stream(base, key, payload, on_token=None, on_reasoning=None, **k):
        payloads.append(payload)
        return ([{"type": "text", "text": "plan: step 1"}], "end_turn")

    monkeypatch.setattr(ab, "_stream", fake_stream)
    out = ab.run_anthropic_agent(
        "plan it", [], _cfg_anthropic(), approve=lambda n, a: True, session="s", plan_mode=True
    )
    assert out == "plan: step 1"
    assert "Approval mode: PLAN" in _json.dumps(payloads[0].get("system", ""))


def _cfg_anthropic():
    return Config(
        provider="anthropic",
        model="claude-sonnet-5",
        base_url="",
        api_key="k",
        max_steps=5,
        temperature=0.2,
    )


def test_run_plan_flag(tmp_path, monkeypatch):
    import sk.cli.commands.run as run_mod

    _sctx(tmp_path, monkeypatch)
    seen = {}

    def fake_run_agent(task, history, cfg, **k):
        seen.update(k)
        assert k["approve"]("write_file", {"path": "x", "content": "hi"}) is False
        assert k["approve"]("exec", {"cmd": "ls"}) is True
        return "step 1: survey, step 2: patch"

    monkeypatch.setattr(run_mod, "run_agent", fake_run_agent)
    from typer.testing import CliRunner

    from sk.cli import app

    res = CliRunner().invoke(app, ["run", "hi", "--plan"])
    assert res.exit_code == 0, res.output
    assert seen.get("plan_mode") is True
    assert "plan" in res.output


def test_run_plan_beats_yes(tmp_path, monkeypatch):
    import sk.cli.commands.run as run_mod

    _sctx(tmp_path, monkeypatch)
    seen = {}

    def fake_run_agent(task, history, cfg, **k):
        seen.update(k)
        assert k["approve"]("edit_file", {"path": "x"}) is False
        return "nope"

    monkeypatch.setattr(run_mod, "run_agent", fake_run_agent)
    from typer.testing import CliRunner

    from sk.cli import app

    res = CliRunner().invoke(app, ["run", "hi", "--yes", "--plan"])
    assert res.exit_code == 0, res.output
    assert seen.get("plan_mode") is True


def test_tui_approve_plan_gate():
    from sk.tui import SidekickTUI

    app = SidekickTUI()
    app.state["plan"] = True
    assert app._approve("write_file", {"path": "x"}) is False
    assert app._approve("exec", {"cmd": "ls"}) is True
    assert app._review_plan("plan", [("edit_file", {"path": "x"})]) is False
    app.state["plan"] = False
    app.state["yolo"] = True
    assert app._approve("write_file", {"path": "x"}) is True


def test_tui_f4_action_flips_plan():
    from sk.tui import SidekickTUI

    app = SidekickTUI()
    assert app.state.get("plan") is False
    app.action_toggle_plan()
    assert app.state.get("plan") is True
    assert app.state.get("yolo") is False
    app.action_toggle_plan()
    assert app.state.get("plan") is False
