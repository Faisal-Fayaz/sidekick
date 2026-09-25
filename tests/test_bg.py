"""Background runs tests (#39): dispatch, worker success/error, spend-cap gate."""

import sk.jobs as jobs
import sk.store as store


def _iso_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_PATH", tmp_path / "jobs.json")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def test_bg_returns_at_once_without_running(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso_jobs(tmp_path, monkeypatch)
    calls: list[list[str]] = []

    class FakePopen:
        def __init__(self, argv, **k):
            calls.append(list(argv))
            assert k.get("start_new_session") is True
            assert "run-bg-worker" in argv

    monkeypatch.setattr(jobs.subprocess, "Popen", FakePopen)
    res = CliRunner().invoke(app, ["run", "do things", "--bg", "--session", "bgs"])
    assert res.exit_code == 0, res.output
    assert "background job job-" in res.output
    assert len(calls) == 1  # returned without waiting for the worker
    saved = jobs.load_jobs()
    assert len(saved) == 1
    job = next(iter(saved.values()))
    assert job["status"] == "running" and job["session"] == "bgs"


def test_bg_json_envelope_has_job_id(monkeypatch, tmp_path):
    import json

    from typer.testing import CliRunner

    from sk.cli import app

    _iso_jobs(tmp_path, monkeypatch)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: type("P", (), {})())
    res = CliRunner().invoke(app, ["run", "do things", "--bg", "--json"])
    assert res.exit_code == 0, res.output
    doc = json.loads(res.output)
    assert doc["ok"] is True and doc["job_id"].startswith("job-")


def test_worker_success_saves_and_notifies(monkeypatch, tmp_path):
    _iso_jobs(tmp_path, monkeypatch)
    import sk.agent as agent

    monkeypatch.setattr(agent, "run_agent", lambda *a, **k: "did the thing")
    noted: list[tuple] = []
    # notify is imported inside run_bg_worker from sk.daemon: patch at source
    monkeypatch.setattr("sk.daemon.notify", lambda t, b, **k: noted.append((t, b)) or "logged")
    jid = jobs.create_job("do things", "w1", "m", False)
    out = jobs.run_bg_worker(jid)
    assert out == "did the thing"
    saved = jobs.load_jobs()[jid]
    assert saved["status"] == "done" and saved["answer"] == "did the thing"
    assert store.get_history("w1", limit=10)[-1]["content"] == "did the thing"
    assert noted and noted[0][0] == "sidekick job done"


def test_worker_error_marks_job(monkeypatch, tmp_path):
    _iso_jobs(tmp_path, monkeypatch)
    import sk.agent as agent

    def _boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(agent, "run_agent", _boom)
    monkeypatch.setattr("sk.daemon.notify", lambda *a, **k: "logged")
    jid = jobs.create_job("do things", "w2", "m", False)
    out = jobs.run_bg_worker(jid)
    assert "llm down" in out
    saved = jobs.load_jobs()[jid]
    assert saved["status"] == "error" and "llm down" in saved["error"]


def test_worker_unknown_job():
    assert "unknown job" in jobs.run_bg_worker("job-nope")


def test_worker_honors_spend_cap(tmp_path, monkeypatch):
    """End-to-end offline: over-cap session blocks before any LLM call (#30 gate)."""
    _iso_jobs(tmp_path, monkeypatch)
    monkeypatch.setattr("sk.daemon.notify", lambda *a, **k: "logged")
    # price this session over the cap: 600k tokens of claude-sonnet-5 @ $2/MTok
    store.save_message("cap1", "user", "x" * 1200000)
    store.save_message("cap1", "assistant", "y" * 1200000)
    store.log_tool_run(
        "cap1", "llm_call", "claude-sonnet-5", True, "anthropic", "api.anthropic.com", True
    )
    assert float(store.usage_stats("cap1")["cost_usd"] or 0.0) > 1.0
    jid = jobs.create_job("do things", "cap1", "m", False, spend_cap=1.0)
    out = jobs.run_bg_worker(jid)
    assert "Spend cap reached" in out
    assert jobs.load_jobs()[jid]["status"] == "done"


def test_jobs_cli_lists(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso_jobs(tmp_path, monkeypatch)
    res = CliRunner().invoke(app, ["jobs"])
    assert res.exit_code == 0 and "no background jobs" in res.output.lower()
    jid = jobs.create_job("do things", "w3", "m", False)
    jobs.finish_job(jid, answer="ok")
    res = CliRunner().invoke(app, ["jobs"])
    assert res.exit_code == 0 and jid in res.output and "done" in res.output
