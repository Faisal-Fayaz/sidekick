"""Config profiles tests (fixes #189): file/env/project precedence, create/
switch/list flows, save targeting, bg threading, migration docs. Offline."""

import os

import pytest

import sk.store as store
from sk.config import PROFILE_ENV, Config


@pytest.fixture(autouse=True)
def _clean_env():
    old = os.environ.get(PROFILE_ENV)
    yield
    if old is None:
        os.environ.pop(PROFILE_ENV, None)
    else:
        os.environ[PROFILE_ENV] = old


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod

    cfgdir = tmp_path / ".sidekick"
    cfgdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfgdir)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfgdir / "config.toml")
    monkeypatch.chdir(tmp_path)
    return cfgdir


def _write_profile(cfgdir, name, body):
    d = cfgdir / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.toml").write_text(body)


def test_profile_replaces_global_file(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    (cfgdir / "config.toml").write_text('provider = "openai"\nmodel = "m-global"\n')
    _write_profile(cfgdir, "work", 'provider = "groq"\nmodel = "m-work"\n')
    assert Config.load().model == "m-global"
    monkeypatch.setenv(PROFILE_ENV, "work")
    cfg = Config.load()
    assert (cfg.provider, cfg.model, cfg.profile) == ("groq", "m-work", "work")


def test_env_wins_over_profile(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    _write_profile(cfgdir, "work", 'provider = "groq"\nmodel = "m-work"\n')
    monkeypatch.setenv(PROFILE_ENV, "work")
    monkeypatch.setenv("SIDEKICK_MODEL", "m-env")
    assert Config.load().model == "m-env"


def test_project_layers_over_profile(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    _write_profile(cfgdir, "work", 'provider = "groq"\nmodel = "m-work"\n')
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".sidekick.toml").write_text('model = "m-proj"\n')
    monkeypatch.chdir(proj)
    monkeypatch.setenv(PROFILE_ENV, "work")
    cfg = Config.load()
    assert (cfg.provider, cfg.model) == ("groq", "m-proj")


def test_missing_and_bad_profile_fail(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    monkeypatch.setenv(PROFILE_ENV, "nope")
    with pytest.raises(RuntimeError, match="no such profile"):
        Config.load()
    monkeypatch.setenv(PROFILE_ENV, "bad name!")
    with pytest.raises(RuntimeError, match="bad profile name"):
        Config.load()


def test_list_profiles(tmp_path, monkeypatch):
    from sk.config import list_profiles

    cfgdir = _iso(tmp_path, monkeypatch)
    assert list_profiles() == []
    _write_profile(cfgdir, "work", 'model = "a"\n')
    _write_profile(cfgdir, "home", 'model = "b"\n')
    assert list_profiles() == ["home", "work"]


def test_save_targets_active_profile(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    (cfgdir / "config.toml").write_text('provider = "ollama"\nmodel = "m-global"\n')
    _write_profile(cfgdir, "work", 'provider = "ollama"\nmodel = "m-work"\n')
    monkeypatch.setenv(PROFILE_ENV, "work")
    cfg = Config.load()
    cfg.model = "m-edited"
    cfg.save()
    assert "m-edited" in (cfgdir / "profiles" / "work.toml").read_text()
    assert "m-edited" not in (cfgdir / "config.toml").read_text()


def test_cli_list_save_switch(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    from typer.testing import CliRunner

    from sk.cli import app

    runner = CliRunner()
    res = runner.invoke(app, ["config", "--profiles"])
    assert "no profiles" in res.output
    res = runner.invoke(app, ["config", "--save-profile", "work"])
    assert res.exit_code == 0, res.output
    assert "profile `work` saved" in res.output
    saved = (tmp_path / ".sidekick" / "profiles" / "work.toml").read_text()
    assert 'api_key = ""' in saved  # snapshot carries no secrets
    res = runner.invoke(app, ["config", "--profiles"])
    assert "work" in res.output
    res = runner.invoke(app, ["--profile", "work", "config", "--show"])
    assert res.exit_code == 0, res.output
    assert "profile=work" in res.output
    res = runner.invoke(app, ["--profile", "nope", "config", "--show"])
    assert res.exit_code == 1 and "no such profile" in res.output


def test_bg_job_carries_profile(tmp_path, monkeypatch):
    import sk.agent as agent
    import sk.jobs as jobs

    monkeypatch.setattr(jobs, "JOBS_PATH", tmp_path / "jobs.json")
    _iso(tmp_path, monkeypatch)
    cfgdir = tmp_path / ".sidekick"
    _write_profile(cfgdir, "work", 'provider = "groq"\nmodel = "m-work"\n')
    monkeypatch.setattr(agent, "run_agent", lambda *a, **k: "done")
    monkeypatch.setattr("sk.daemon.notify", lambda *a, **k: "logged")
    jid = jobs.create_job("t", "s", "m", False, 0.0, (), False, False, "work")
    assert jobs.load_jobs()[jid]["profile"] == "work"
    seen = {}

    def fake_run_agent(task, history, cfg, **k):
        seen["provider"] = cfg.provider
        return "done"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)
    jobs.run_bg_worker(jid)
    assert seen["provider"] == "groq"
