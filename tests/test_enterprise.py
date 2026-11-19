"""The local enterprise surface: identity, tool policy and an offline report (#513)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import sk.agent as agent
import sk.enterprise as ent
import sk.store as store
from sk.cli import app


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from sk import config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    for key in ("SIDEKICK_ACTOR", "SIDEKICK_TEAM"):
        monkeypatch.delenv(key, raising=False)
    yield tmp_path


# --- identity and policy ---------------------------------------------------


def test_identity_is_empty_by_default_and_env_supplied(monkeypatch):
    assert ent.identity() == ("", "")
    monkeypatch.setenv("SIDEKICK_ACTOR", "alice@example.com")
    monkeypatch.setenv("SIDEKICK_TEAM", "platform")
    assert ent.identity() == ("alice@example.com", "platform")


def test_deny_tools_comes_from_the_global_file_only(isolated):
    (isolated / "config.toml").write_text('deny_tools = ["shell", "write_file"]\n')
    assert ent.denied_tools() == ("shell", "write_file")
    assert "denied" in ent.check_policy("shell")
    assert ent.check_policy("read_file") == ""

    # A repo copy is ignored, so the project cannot deny (or allow) anything.
    (isolated / ".sidekick.toml").write_text('deny_tools = ["read_file"]\n')
    assert ent.denied_tools() == ("shell", "write_file")


def test_a_broken_policy_is_ignored(isolated):
    (isolated / "config.toml").write_text("deny_tools = 42\n")
    assert ent.denied_tools() == ()


def test_policy_denial_is_enforced_and_audited(isolated, monkeypatch):
    (isolated / "config.toml").write_text('deny_tools = ["read_url"]\n')
    called = []
    monkeypatch.setattr(agent, "dispatch_tool", lambda n, a: called.append(n) or "ran")
    result, ok = agent._gated_dispatch("read_url", {"url": "https://example.com"}, session="policy")
    assert ok is False and "denied by" in result
    assert called == []
    rows = store.list_tool_runs("policy")
    assert any("policy=deny" in str(r["target"]) for r in rows)


def test_actor_is_stamped_into_new_audit_rows(isolated, monkeypatch):
    monkeypatch.setenv("SIDEKICK_ACTOR", "alice")
    store.log_tool_run("s", "read_file", "x")
    assert store.list_tool_runs("s")[0]["actor"] == "alice"


# --- report rendering -----------------------------------------------------


def _rows():
    return [
        {
            "session": "s1",
            "tool": "read_url",
            "target": "taint=approve origin=web page url=https://x.test sk-or-v1-secret",
            "approved": 1,
            "provider": "openrouter",
            "host": "x.test",
            "ok": 1,
            "ts": 1.0,
            "actor": "alice",
        },
        {
            "session": "s2",
            "tool": "shell",
            "target": "policy=deny shell|rm",
            "approved": 0,
            "provider": "",
            "host": "",
            "ok": 0,
            "ts": 2.0,
            "actor": "",
        },
        {"bad": "row"},
    ]


def test_markdown_report_has_counts_sessions_and_redaction():
    text = ent.render_report(_rows(), format="md")
    assert "# Sidekick audit report" in text
    assert "2 runs" in text and "1 denied" in text and "1 taint decisions" in text
    assert "s1" in text and "alice" in text and "(none)" in text
    assert "sk-or-v1-secret" not in text and "<redacted>" in text


def test_html_report_is_self_contained_and_escaped():
    text = ent.render_report(_rows(), format="html")
    assert text.startswith("<!doctype html>")
    assert "http://" not in text and "https://" not in text and "<script" not in text
    assert "sk-or-v1-secret" not in text


def test_an_empty_ledger_still_renders():
    text = ent.render_report([])
    assert "0 runs" in text and "(none)" in text


def test_session_filter_limits_the_report():
    text = ent.render_report(_rows(), session="s2")
    assert "1 runs" in text and "shell" in text and "read_url" not in text


def test_report_cli_writes_a_file(isolated, tmp_path):
    store.log_tool_run("s", "read_file", "note")
    out = tmp_path / "report.md"
    result = CliRunner().invoke(app, ["audit", "report", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "# Sidekick audit report" in out.read_text()


def test_audit_command_still_lists_rows(isolated):
    store.log_tool_run("s", "read_file", "note")
    result = CliRunner().invoke(app, ["audit", "--session", "s"])
    assert result.exit_code == 0, result.output
    assert "read_file" in result.output
