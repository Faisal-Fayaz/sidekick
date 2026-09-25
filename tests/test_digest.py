"""Teammate-pilot digest tests (#41): composition, no-LLM, DND, state, CLI."""

import sk.daemon as daemon
import sk.store as store


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "STATE_PATH", tmp_path / "daemon.json")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def test_digest_composes_brief_and_failures(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    store.log_shell("nightly boom", "/tmp", 1)
    text, state = daemon.digest_text({}, projects=[])
    assert "**brief**" in text
    assert "nightly boom" in text
    assert state["last_shell_id"] > 0


def test_digest_advances_state(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    store.log_shell("once only", "/tmp", 2)
    text1, state = daemon.digest_text({}, projects=[])
    assert "once only" in text1
    text2, _ = daemon.digest_text(state, projects=[])
    assert "once only" not in text2


def test_digest_never_calls_llm(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("digest must stay deterministic (no LLM)")

    import sk.agent as agent

    monkeypatch.setattr(agent, "run_agent", _boom)
    text, _ = daemon.digest_text({}, projects=[])
    assert "**brief**" in text


def test_deliver_dnd_logs(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    monkeypatch.setattr(daemon, "in_dnd", lambda *a, **k: True)
    logged: list[str] = []
    monkeypatch.setattr(daemon, "append_log", lambda ns: logged.extend(ns))
    outcome, text = daemon.deliver_digest(projects=[])
    assert "quiet hours" in outcome and "**brief**" in text
    assert logged  # digest preserved to nudges.log


def test_deliver_no_backend_logs(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    monkeypatch.setattr(daemon, "in_dnd", lambda *a, **k: False)
    monkeypatch.setattr(daemon, "notify_backend", lambda: "")
    outcome, text = daemon.deliver_digest(projects=[])
    assert "logged to nudges.log" in outcome and text


def test_deliver_sent_path(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    monkeypatch.setattr(daemon, "in_dnd", lambda *a, **k: False)
    monkeypatch.setattr(daemon, "notify_backend", lambda: "notify-send")
    monkeypatch.setattr(
        daemon.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    outcome, _ = daemon.deliver_digest(projects=[])
    assert "delivered via notify-send" in outcome


def test_digest_cli(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso(tmp_path, monkeypatch)
    store.log_shell("cli boom", "/tmp", 1)
    monkeypatch.setattr(daemon, "in_dnd", lambda *a, **k: True)
    res = CliRunner().invoke(app, ["digest"])
    assert res.exit_code == 0, res.output
    assert "brief" in res.output.lower() and "quiet hours" in res.output
