"""Models sub-app tests: list/pull/prune fully mocked. No downloads."""

import sk.cli.commands.system as sys_mod


def _runner():
    from typer.testing import CliRunner

    from sk.cli import app

    return CliRunner(), app


def _ollama_cfg(monkeypatch, models):
    monkeypatch.setattr("sk.auth.fetch_models", lambda *a, **k: list(models))


def test_bare_and_list_show_models(monkeypatch):
    _ollama_cfg(monkeypatch, ["m-a", "m-b"])
    runner, app = _runner()
    for argv in (["models"], ["models", "list"]):
        res = runner.invoke(app, argv)
        assert res.exit_code == 0, res.output
        assert "m-a" in res.output and "m-b" in res.output


def test_pull_installs_and_verifies(monkeypatch):

    _ollama_cfg(monkeypatch, ["qwen3:4b"])
    monkeypatch.setattr("sk.init_wizard.pull_model", lambda *a, **k: (True, "pulled"))
    runner, app = _runner()
    res = runner.invoke(app, ["models", "pull", "qwen3:4b"])
    assert res.exit_code == 0, res.output
    assert "installed" in res.output


def test_pull_failure_and_non_ollama(monkeypatch):
    _ollama_cfg(monkeypatch, [])
    monkeypatch.setattr("sk.init_wizard.pull_model", lambda *a, **k: (False, "boom"))
    runner, app = _runner()
    res = runner.invoke(app, ["models", "pull", "qwen3:4b"])
    assert res.exit_code != 0 and "boom" in res.output
    monkeypatch.setattr(
        "sk.auth.fetch_models", lambda *a, **k: (_ for _ in ()).throw(Exception("down"))
    )
    from sk.config import Config

    monkeypatch.setattr(
        sys_mod, "_cfg", lambda: Config(provider="groq", model="m", base_url="", api_key="")
    )
    res = runner.invoke(app, ["models", "pull", "qwen3:4b"])
    assert res.exit_code != 0 and "ollama" in res.output.lower()


def test_prune_confirm_flow(monkeypatch):
    _ollama_cfg(monkeypatch, ["keep"])
    import subprocess

    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/ollama")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stderr": ""})()
    )
    runner, app = _runner()
    res = runner.invoke(app, ["models", "prune", "gone", "--yes"])
    assert res.exit_code == 0, res.output
    assert "removed" in res.output


def test_prune_abort_and_failure(monkeypatch):
    _ollama_cfg(monkeypatch, ["m"])
    runner, app = _runner()
    res = runner.invoke(app, ["models", "prune", "m"], input="n\n")
    assert res.exit_code == 0 and "aborted" in res.output.lower()
    import subprocess

    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/ollama")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 1, "stderr": "nope"})()
    )
    res = runner.invoke(app, ["models", "prune", "m", "--yes"])
    assert res.exit_code != 0 and "nope" in res.output


def test_brief_disk_warning_points_at_prune():
    from sk.brief import format_brief_text

    out = format_brief_text({"when": "t", "sysinfo": "x 95% / y", "projects": []})
    assert "95% full" in out and "sk models prune" in out
