"""Session allowlist tests (#40): parsing, matching, approver + bg forwarding."""

from sk.cli.approvers import _make_approver, _make_approver_state
from sk.config import is_session_allowed, parse_allow_list


def test_parse_allow_list():
    assert parse_allow_list("") == ()
    assert parse_allow_list("shell:pytest,write_file") == ("shell:pytest", "write_file")
    assert parse_allow_list("shell:pytest write_file") == ("shell:pytest", "write_file")
    assert parse_allow_list(["shell:pytest", " write_file "]) == ("shell:pytest", "write_file")
    assert parse_allow_list(None) == ()
    assert parse_allow_list(",,,") == ()


def test_tool_name_match():
    assert is_session_allowed("write_file", {"path": "x"}, ("write_file",)) is True
    assert is_session_allowed("shell", {"cmd": "ls"}, ("write_file",)) is False
    assert is_session_allowed("write_file", {"path": "x"}, ()) is False


def test_shell_prefix_word_boundary():
    assert is_session_allowed("shell", {"cmd": "pytest -q"}, ("shell:pytest",)) is True
    assert is_session_allowed("shell", {"cmd": "pytest"}, ("shell:pytest",)) is True
    assert is_session_allowed("shell", {"cmd": "pytest-x"}, ("shell:pytest",)) is False
    assert is_session_allowed("shell", {"cmd": "make test"}, ("shell:pytest",)) is False
    assert is_session_allowed("shell", {"cmd": "rm x"}, ("shell",)) is True  # bare = all shell


def test_scope_mismatch_and_garbage():
    assert is_session_allowed("shell", {"cmd": "pytest"}, ("write_file:pytest",)) is False
    assert is_session_allowed("shell", {"cmd": "pytest"}, ("shell:",)) is False
    assert is_session_allowed("shell", {"cmd": "pytest"}, None) is False
    assert is_session_allowed("shell", {}, ("shell:pytest",)) is False


def test_approver_allow_skips_prompt(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("must not prompt")

    monkeypatch.setattr("sk.cli.approvers.typer.confirm", _boom)
    approve = _make_approver(False, (), ("shell:pytest",))
    assert approve("shell", {"cmd": "pytest -q"}) is True


def test_approver_non_allow_still_prompts(monkeypatch):
    monkeypatch.setattr("sk.cli.approvers.typer.confirm", lambda *a, **k: False)
    approve = _make_approver(False, (), ("shell:pytest",))
    assert approve("shell", {"cmd": "rm -rf /tmp/x"}) is False
    assert approve("write_file", {"path": "x", "content": "y"}) is False


def test_state_approver_honors_allow(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("must not prompt")

    monkeypatch.setattr("sk.cli.approvers.typer.confirm", _boom)
    approve = _make_approver_state({"yolo": False}, (), ("write_file",))
    assert approve("write_file", {"path": "x", "content": "y"}) is True


def test_bg_job_carries_allow(monkeypatch, tmp_path):
    import sk.jobs as jobs
    import sk.store as store

    monkeypatch.setattr(jobs, "JOBS_PATH", tmp_path / "jobs.json")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    seen: dict = {}

    def fake_approver(auto_yes, preapproved=(), allow=()):
        seen["allow"] = tuple(allow)
        return lambda *a, **k: True

    monkeypatch.setattr("sk.cli.approvers._make_approver", fake_approver)
    import sk.agent as agent

    monkeypatch.setattr(agent, "run_agent", lambda *a, **k: "ok")
    monkeypatch.setattr("sk.daemon.notify", lambda *a, **k: "logged")
    jid = jobs.create_job("t", "s", "m", False, 0.0, ("shell:pytest",))
    assert jobs.load_jobs()[jid]["allow"] == ["shell:pytest"]
    jobs.run_bg_worker(jid)
    assert seen["allow"] == ("shell:pytest",)


def test_run_bg_allow_cli(monkeypatch, tmp_path):
    import sk.jobs as jobs
    import sk.store as store

    from typer.testing import CliRunner

    from sk.cli import app

    monkeypatch.setattr(jobs, "JOBS_PATH", tmp_path / "jobs.json")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: type("P", (), {})())
    res = CliRunner().invoke(app, ["run", "t", "--bg", "--allow", "shell:pytest,write_file"])
    assert res.exit_code == 0, res.output
    job = next(iter(jobs.load_jobs().values()))
    assert job["allow"] == ["shell:pytest", "write_file"]
