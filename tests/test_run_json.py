"""sk run --json tests: envelope schema, quietness, exit codes. No model needed."""

import json

import sk.cli.commands.run as run_mod


def _runner():
    from typer.testing import CliRunner

    from sk.cli import app

    return CliRunner(), app


def test_json_envelope_and_tools(monkeypatch):
    def fake_run_agent(task, history, cfg, **k):
        k["on_tool"]("exec", {"cmd": "ls"})
        k["on_tool"]("exec", {"cmd": "ls"})  # dupes collapse
        k["on_tool"]("list_dir", {"path": "."})
        return "all done here"

    monkeypatch.setattr(run_mod, "run_agent", fake_run_agent)
    runner, app = _runner()
    res = runner.invoke(app, ["run", "hi", "--json", "--yes"])
    assert res.exit_code == 0, res.output
    doc = json.loads(res.output)  # stdout is exactly one JSON document
    assert doc["ok"] is True and doc["answer"] == "all done here"
    assert doc["tools"] == ["exec", "list_dir"]
    assert doc["error"] is None and doc["session"] == "default"


def test_json_failure_exit_code(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(run_mod, "run_agent", boom)
    runner, app = _runner()
    res = runner.invoke(app, ["run", "hi", "--json", "--yes"])
    assert res.exit_code == 1
    doc = json.loads(res.output)
    assert doc["ok"] is False and "unreachable" in (doc["error"] or "")


def test_json_denial_is_completed_turn(monkeypatch):
    monkeypatch.setattr(
        run_mod, "run_agent", lambda *a, **k: "Denied: plan denied by user — nothing was executed."
    )
    runner, app = _runner()
    res = runner.invoke(app, ["run", "hi", "--json", "--yes"])
    assert res.exit_code == 0
    assert json.loads(res.output)["answer"].startswith("Denied: plan denied")


def test_human_output_unchanged(monkeypatch):
    monkeypatch.setattr(run_mod, "run_agent", lambda *a, **k: "hi there")
    runner, app = _runner()
    res = runner.invoke(app, ["run", "hi", "--yes"])
    assert res.exit_code == 0, res.output
    assert "--- done ---" in res.output
    try:
        json.loads(res.output)
        human_is_json = True
    except Exception:
        human_is_json = False
    assert human_is_json is False  # human mode stays human


def test_resolve_quiet_suppresses_router_print(capsys):
    from sk.cli.resolve import _resolve_model
    from sk.config import Config

    cfg = Config(provider="ollama", model="m", base_url="", api_key="")
    assert _resolve_model(cfg, "auto", "do something code-like here", quiet=True) in (
        "llama3.2:3b",
        "qwen2.5-coder:7b",
    )
    assert capsys.readouterr().out == ""


def test_json_never_carries_reasoning_callback(monkeypatch):
    """--json purity: on_reasoning must be None so thinking never corrupts output."""
    seen = {}

    def fake_run_agent(task, history, cfg, **k):
        seen.update(k)
        return "done"

    monkeypatch.setattr(run_mod, "run_agent", fake_run_agent)
    runner, app = _runner()
    res = runner.invoke(app, ["run", "hi", "--json", "--yes"])
    assert res.exit_code == 0, res.output
    assert seen.get("on_reasoning") is None
    json.loads(res.output)


def test_plain_run_wires_reasoning_callback(monkeypatch):
    seen = {}

    def fake_run_agent(task, history, cfg, **k):
        seen.update(k)
        return "done"

    monkeypatch.setattr(run_mod, "run_agent", fake_run_agent)
    runner, app = _runner()
    res = runner.invoke(app, ["run", "hi", "--yes", "--model", "fast"])
    assert res.exit_code == 0, res.output
    assert callable(seen.get("on_reasoning"))


def test_auto_fast_exhaustion_escalates_to_smart_once(monkeypatch):
    """Refs #275: auto-routed fast turn that exhausts retries once on smart."""
    from sk.config import TIERS

    calls = []

    def fake_run_agent(task, history, cfg, **k):
        calls.append(cfg.model)
        return "(max steps reached)" if len(calls) == 1 else "smart answer"

    monkeypatch.setattr(run_mod, "run_agent", fake_run_agent)
    runner, app = _runner()
    res = runner.invoke(app, ["run", "write me a poem", "--json", "--yes"])
    assert res.exit_code == 0, res.output
    doc = json.loads(res.output)
    assert doc["answer"] == "smart answer"
    assert len(calls) == 2  # single retry: no ping-pong
    assert calls[1] == TIERS["ollama"]["smart"]


def test_auto_fast_good_answer_runs_once(monkeypatch):
    calls = []

    def fake_run_agent(task, history, cfg, **k):
        calls.append(cfg.model)
        return "a fine poem"

    monkeypatch.setattr(run_mod, "run_agent", fake_run_agent)
    runner, app = _runner()
    res = runner.invoke(app, ["run", "write me a poem", "--json", "--yes"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["answer"] == "a fine poem"
    assert len(calls) == 1


def test_explicit_model_never_escalates(monkeypatch):
    calls = []

    def fake_run_agent(task, history, cfg, **k):
        calls.append(cfg.model)
        return "(max steps reached)"

    monkeypatch.setattr(run_mod, "run_agent", fake_run_agent)
    runner, app = _runner()
    res = runner.invoke(app, ["run", "write me a poem", "--json", "--yes", "--model", "fast"])
    assert res.exit_code == 0, res.output
    assert len(calls) == 1  # explicit picks are the user's responsibility
