"""Entry-point tests: bare `sk` launches the TUI; `sk chat` stays as fallback."""

from typer.testing import CliRunner

from sk.cli import app


def test_bare_invocation_launches_tui(monkeypatch):
    calls: list[dict] = []
    # _default() resolves `launch` lazily (`from sk.tui import launch`),
    # so patching the module attribute intercepts the call.
    monkeypatch.setattr("sk.tui.launch", lambda *a, **k: calls.append({"a": a, "k": k}))
    res = CliRunner().invoke(app, [])
    assert res.exit_code == 0, res.output
    assert len(calls) == 1


def test_chat_help_still_available():
    res = CliRunner().invoke(app, ["chat", "--help"])
    assert res.exit_code == 0 and "REPL" in res.output
