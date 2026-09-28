"""Persistent shell sessions tests (fixes #179): cwd/env persistence,
isolation, exit codes, timeout re-sync, refusals, approval. Offline (bash)."""

import pytest

import sk.store as store
from sk.tools.shell import _close_all_sessions, tool_shell_session


@pytest.fixture(autouse=True)
def _clean_sessions():
    yield
    _close_all_sessions()


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    monkeypatch.chdir(tmp_path)


def test_cwd_and_env_persist(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    (tmp_path / "sub").mkdir()
    assert "sub" in tool_shell_session("cd sub && pwd", session="t-persist")
    out = tool_shell_session("export FOO=bar123 && echo got:$FOO", session="t-persist")
    assert "got:bar123" in out
    out = tool_shell_session("echo $FOO && pwd", session="t-persist")
    assert "bar123" in out and out.rstrip().endswith("sub")


def test_exit_codes_and_format(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    out = tool_shell_session("echo hi", session="t-rc")
    assert out.startswith("$ echo hi\n[exit 0]\n") and out.endswith("hi")
    out = tool_shell_session("false", session="t-rc")
    assert "[exit 1]" in out


def test_sessions_isolated(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    tool_shell_session("export V=aaa", session="t-a")
    tool_shell_session("export V=bbb", session="t-b")
    assert "aaa" in tool_shell_session("echo $V", session="t-a")
    assert "bbb" in tool_shell_session("echo $V", session="t-b")


def test_timeout_keeps_session_and_resyncs(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    out = tool_shell_session("sleep 6", session="t-slow", timeout=5)
    assert "timed out after 5s" in out and "session kept" in out
    out = tool_shell_session("echo back", session="t-slow", timeout=10)
    assert "[exit 0]" in out and out.rstrip().endswith("back") and "__SK_" not in out


def test_hard_refusal_and_empty(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    assert "refused" in tool_shell_session("rm -rf /", session="t-x")
    assert "empty command" in tool_shell_session("   ", session="t-x")


def test_exit_closes_session(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    (tmp_path / "sub").mkdir()
    tool_shell_session("cd sub", session="t-e")
    assert tool_shell_session("exit", session="t-e").startswith("Closed shell session")
    out = tool_shell_session("pwd", session="t-e")
    assert not out.rstrip().endswith("sub")


def test_output_capped(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    out = tool_shell_session("seq 1 3000", session="t-cap")
    assert "[truncated]" in out


def test_approval_gating(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    from sk.cli.approvers import _make_approver
    from sk.tools import approval_tools

    assert "shell_session" in approval_tools()
    assert _make_approver(False, (), (), readonly=True)("shell_session", {"cmd": "ls"}) is False
    import typer

    monkeypatch.setattr(typer, "confirm", lambda *a, **k: True)
    assert _make_approver(False, (), (), plan_mode=True)("shell_session", {"cmd": "ls"}) is True


def test_dispatch_and_schema(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    from sk.tools import dispatch_tool, tools_schema

    out = dispatch_tool("shell_session", {"cmd": "echo via-dispatch", "session": "t-d"})
    assert "via-dispatch" in out
    entry = next(e for e in tools_schema() if e["function"]["name"] == "shell_session")
    assert entry["function"]["parameters"]["required"] == ["cmd"]
