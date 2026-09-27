"""Read-only approval mode tests (fixes #98): approver denial, plan review,
slash toggles, prompt line, `sk run --read-only`, TUI gate. Fully offline."""

import sk.slash as slash
from sk.agent import build_messages
from sk.cli.approvers import _make_approver, _make_approver_state, _make_plan_reviewer
from sk.config import Config

WRITES = ["write_file", "edit_file", "delete_file", "make_dir", "shell"]
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
    return {"session": "test", "cfg": _cfg(), "state": {"yolo": False, "readonly": False}}


def test_one_shot_approver_denies_writes_in_readonly():
    approve = _make_approver(False, (), (), readonly=True)
    for tool in WRITES:
        assert approve(tool, {"path": "x", "cmd": "x"}) is False
    for tool in READS:
        assert approve(tool, {"path": "x", "cmd": "ls"}) is True


def test_state_approver_readonly_beats_yolo_and_allowlists():
    approve = _make_approver_state({"yolo": True, "readonly": True}, (), ("write_file",))
    assert approve("write_file", {"path": "x"}) is False
    assert approve("shell", {"cmd": "ls"}) is False
    assert approve("exec", {"cmd": "ls"}) is True


def test_state_approver_confirm_still_asks(monkeypatch):
    import typer

    monkeypatch.setattr(typer, "confirm", lambda *a, **k: True)
    approve = _make_approver_state({"yolo": False}, (), ())
    assert approve("write_file", {"path": "x", "content": "hi"}) is True


def test_plan_reviewer_denies_write_plans_in_readonly():
    review = _make_plan_reviewer({"readonly": True})
    assert review("plan", [("write_file", {"path": "x"})]) is False
    assert review("plan", [("shell", {"cmd": "rm x"})]) is False


def test_plan_reviewer_allows_read_plans_in_readonly(monkeypatch):
    import typer

    monkeypatch.setattr(typer, "confirm", lambda *a, **k: True)
    review = _make_plan_reviewer({"readonly": True})
    assert review("plan", [("exec", {"cmd": "ls"})]) is True


def test_slash_readonly_toggle(tmp_path, monkeypatch):
    c = _sctx(tmp_path, monkeypatch)
    out = slash.handle("/readonly", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled is True
    assert c["state"] == {"yolo": False, "readonly": True, "plan": False}
    out = slash.handle("/yolo", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert c["state"] == {"yolo": True, "readonly": False, "plan": False}
    slash.handle("/readonly", session=c["session"], cfg=c["cfg"], state=c["state"])
    out = slash.handle("/confirm", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled is True
    assert c["state"] == {"yolo": False, "readonly": False, "plan": False}


def test_help_lists_readonly(tmp_path, monkeypatch):
    c = _sctx(tmp_path, monkeypatch)
    out = slash.handle("/help", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert "/readonly" in out.text


def test_build_messages_readonly_line(tmp_path, monkeypatch):
    _sctx(tmp_path, monkeypatch)
    sys_ro = build_messages("hi", [], _cfg(), read_only=True)[0]["content"]
    assert "Approval mode: READ-ONLY" in sys_ro
    sys_conf = build_messages("hi", [], _cfg())[0]["content"]
    assert "Approval mode: READ-ONLY" not in sys_conf
    assert "Approval mode: CONFIRM" in sys_conf


def test_run_read_only_flag(tmp_path, monkeypatch):
    import sk.cli.commands.run as run_mod

    _sctx(tmp_path, monkeypatch)
    seen = {}

    def fake_run_agent(task, history, cfg, **k):
        seen.update(k)
        assert k["approve"]("write_file", {"path": "x", "content": "hi"}) is False
        assert k["approve"]("exec", {"cmd": "ls"}) is True
        return "looked, did not touch"

    monkeypatch.setattr(run_mod, "run_agent", fake_run_agent)
    from typer.testing import CliRunner

    from sk.cli import app

    res = CliRunner().invoke(app, ["run", "hi", "--read-only"])
    assert res.exit_code == 0, res.output
    assert seen.get("read_only") is True
    assert "read-only" in res.output


def test_run_read_only_beats_yes(tmp_path, monkeypatch):
    import sk.cli.commands.run as run_mod

    _sctx(tmp_path, monkeypatch)
    seen = {}

    def fake_run_agent(task, history, cfg, **k):
        seen.update(k)
        assert k["approve"]("shell", {"cmd": "rm -rf /tmp/x"}) is False
        return "nope"

    monkeypatch.setattr(run_mod, "run_agent", fake_run_agent)
    from typer.testing import CliRunner

    from sk.cli import app

    res = CliRunner().invoke(app, ["run", "hi", "--yes", "--read-only"])
    assert res.exit_code == 0, res.output
    assert seen.get("read_only") is True


def test_tui_approve_denies_in_readonly():
    from sk.tui import SidekickTUI

    app = SidekickTUI()
    app.state["readonly"] = True
    assert app._approve("write_file", {"path": "x"}) is False
    assert app._approve("exec", {"cmd": "ls"}) is True
    app.state["readonly"] = False
    app.state["yolo"] = True
    assert app._approve("write_file", {"path": "x"}) is True


def test_tui_plan_review_denies_writes_in_readonly():
    from sk.tui import SidekickTUI

    app = SidekickTUI()
    app.state["readonly"] = True
    assert app._review_plan("plan", [("write_file", {"path": "x"})]) is False
