"""Audit tests: tool_runs logging, local-vs-egress, sk audit md/json. Fully offline."""

import sk.store as store
from sk.agent import _gated_dispatch


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def test_log_list_roundtrip(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    store.log_tool_run(
        "s1", "write_file", "write_file|path=/tmp/x", True, "ollama", "localhost", True
    )
    store.log_tool_run("s1", "shell", "shell|cmd=rm -rf /", False, "groq", "api.groq.com", False)
    rows = store.list_tool_runs("s1")
    assert len(rows) == 2
    assert rows[0]["tool"] == "shell" and rows[0]["approved"] == 0  # newest first
    assert rows[1]["approved"] == 1 and rows[1]["ok"] == 1
    assert store.list_tool_runs("other") == []
    assert len(store.list_tool_runs("")) == 2  # empty session = all


def test_is_local_traffic_table():
    assert store.is_local_traffic("ollama", "localhost") is True
    assert store.is_local_traffic("lmstudio", "anything.example.com") is True
    assert store.is_local_traffic("groq", "localhost") is True
    assert store.is_local_traffic("openai", "192.168.1.5") is True
    assert store.is_local_traffic("openai", "10.0.0.2") is True
    assert store.is_local_traffic("openai", "172.16.0.9") is True
    assert store.is_local_traffic("openai", "172.15.0.9") is False
    assert store.is_local_traffic("groq", "api.groq.com") is False
    assert store.is_local_traffic("openai", "api.openai.com") is False


def test_gated_dispatch_logs_allowed_and_denied(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    out, ok = _gated_dispatch(
        "list_dir",
        {"path": "/tmp"},
        approve=lambda n, a: True,
        session="s",
        provider="ollama",
        host="localhost",
    )
    assert ok is True
    out, ok = _gated_dispatch(
        "shell",
        {"cmd": "echo hi"},
        approve=lambda n, a: False,
        session="s",
        provider="groq",
        host="api.groq.com",
    )
    assert ok is False and "Denied" in out
    rows = store.list_tool_runs("s")
    assert len(rows) == 2
    denied = [r for r in rows if r["approved"] == 0]
    assert len(denied) == 1 and denied[0]["tool"] == "shell"


def test_audit_cli_md_and_json(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso(tmp_path, monkeypatch)
    store.log_tool_run(
        "s", "read_url", "read_url|url=https://example.com", True, "ollama", "localhost", True
    )
    res = CliRunner().invoke(app, ["audit", "--session", "s"])
    assert res.exit_code == 0, res.output
    assert "1 local" in res.output and "0 egress" in res.output and "read_url" in res.output
    res = CliRunner().invoke(app, ["audit", "--format", "json"])
    assert res.exit_code == 0, res.output
    import json

    rows = json.loads(res.output)
    assert rows and rows[0]["tool"] == "read_url"
    res = CliRunner().invoke(app, ["audit", "--session", "nosuch"])
    assert res.exit_code == 0 and "no tool runs" in res.output.lower()
