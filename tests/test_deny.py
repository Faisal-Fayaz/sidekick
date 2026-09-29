"""Session denylist tests (#234): matching, precedence, approver + bg forwarding."""

from sk.cli.approvers import _make_approver, _make_approver_state
from sk.config import is_session_denied


def test_tool_name_match():
    assert is_session_denied("write_file", {"path": "x"}, ("write_file",)) is True
    assert is_session_denied("shell", {"cmd": "ls"}, ("write_file",)) is False
    assert is_session_denied("write_file", {"path": "x"}, ()) is False


def test_shell_prefix_word_boundary():
    assert is_session_denied("shell", {"cmd": "rm -rf /"}, ("shell:rm",)) is True
    assert is_session_denied("shell", {"cmd": "rm"}, ("shell:rm",)) is True
    assert is_session_denied("shell", {"cmd": "rmx"}, ("shell:rm",)) is False
    assert is_session_denied("shell", {"cmd": "pytest -q"}, ("shell:rm",)) is False
    assert is_session_denied("shell", {"cmd": "ls"}, ("shell",)) is True  # bare = all shell


def test_scope_mismatch_and_garbage():
    assert is_session_denied("shell", {"cmd": "rm"}, ("write_file:rm",)) is False
    assert is_session_denied("shell", {"cmd": "rm"}, ("shell:",)) is False
    assert is_session_denied("shell", {"cmd": "rm"}, None) is False
    assert is_session_denied("shell", {}, ("shell:rm",)) is False


def test_deny_beats_allow_and_yolo(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("denied calls must never prompt")

    monkeypatch.setattr("sk.cli.approvers.typer.confirm", _boom)
    approve = _make_approver(False, (), ("shell",), deny=("shell:rm",))
    assert approve("shell", {"cmd": "rm -rf /"}) is False
    assert approve("shell", {"cmd": "ls"}) is True
    # deny beats yolo too: explicit denial is the strongest signal
    approve_yolo = _make_approver_state({"yolo": True}, (), ("shell",), ("shell:rm",))
    assert approve_yolo("shell", {"cmd": "rm -rf /"}) is False
    assert approve_yolo("shell", {"cmd": "ls"}) is True


def test_deny_beats_project_approval(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("denied calls must never prompt")

    monkeypatch.setattr("sk.cli.approvers.typer.confirm", _boom)
    approve = _make_approver(False, ("rm -rf /",), (), deny=("shell:rm",))
    assert approve("shell", {"cmd": "rm -rf /tmp"}) is False


def test_state_approver_honors_live_deny(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("denied calls must never prompt")

    monkeypatch.setattr("sk.cli.approvers.typer.confirm", _boom)
    # live state deny (e.g. populated by TUI) applies without re-construction
    state = {"yolo": False, "deny": ("write_file",)}
    approve2 = _make_approver_state(state, (), (), ())
    assert approve2("write_file", {"path": "x", "content": "y"}) is False


def test_bg_job_carries_deny(monkeypatch, tmp_path):
    import sk.jobs as jobs
    import sk.store as store

    monkeypatch.setattr(jobs, "JOBS_PATH", tmp_path / "jobs.json")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    seen: dict = {}

    def fake_approver(auto_yes, preapproved=(), allow=(), readonly=False, plan_mode=False, deny=()):
        seen["deny"] = tuple(deny)
        return lambda *a, **k: True

    monkeypatch.setattr("sk.cli.approvers._make_approver", fake_approver)
    import sk.agent as agent

    monkeypatch.setattr(agent, "run_agent", lambda *a, **k: "ok")
    monkeypatch.setattr("sk.daemon.notify", lambda *a, **k: "logged")
    jid = jobs.create_job("t", "s", "m", False, 0.0, (), deny=("shell",))
    assert jobs.load_jobs()[jid]["deny"] == ["shell"]
    jobs.run_bg_worker(jid)
    assert seen["deny"] == ("shell",)


def test_run_bg_deny_cli(monkeypatch, tmp_path):
    import sk.jobs as jobs
    import sk.store as store

    from typer.testing import CliRunner

    from sk.cli import app

    monkeypatch.setattr(jobs, "JOBS_PATH", tmp_path / "jobs.json")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: type("P", (), {})())
    res = CliRunner().invoke(app, ["run", "t", "--bg", "--deny", "shell,write_file"])
    assert res.exit_code == 0, res.output
    job = next(iter(jobs.load_jobs().values()))
    assert job["deny"] == ["shell", "write_file"]
