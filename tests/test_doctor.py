"""doctor --fix + sk report tests (#70): remediation paths, redaction. No network."""

import sk.cli.commands.system as system
from sk.config import Config


def _cfg(**kw):
    base = {
        "provider": "ollama",
        "model": "qwen2.5-coder:7b",
        "base_url": "",
        "api_key": "",
        "max_steps": 5,
        "temperature": 0.2,
    }
    base.update(kw)
    return Config(**base)


def test_repair_creates_missing_config(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    first = system.repair_config()
    assert first is not None and "created default config" in first
    # second call: healthy now
    assert system.repair_config() is None


def test_repair_resets_corrupt_config(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    path = tmp_path / ".sidekick" / "config.toml"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('max_steps = "not-a-number"\n')  # valid TOML, crashes Config.load
    msg = system.repair_config()
    assert msg is not None and "backed up" in msg
    assert path.with_suffix(".bak").exists()
    assert system.repair_config() is None  # healthy after reset
    assert Config.load().provider == "ollama"


def test_repair_healthy_is_none(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    _cfg().save()
    assert system.repair_config() is None


def test_doctor_fix_pulls_missing_model(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from sk.cli import app

    import sk.auth as auth

    pulled: list[str] = []
    import sk.init_wizard as wizard

    monkeypatch.setattr(auth, "provider_status", lambda cfg: (True, "ok"))
    monkeypatch.setattr(
        wizard, "pull_model", lambda name, **k: pulled.append(name) or (True, "pulled")
    )
    # check (missing -> pull) then re-verify (present)
    calls = {"n": 0}

    def fake_fetch(*a, **k):
        calls["n"] += 1
        if calls["n"] >= 2:
            return ["other-model", "qwen2.5-coder:7b"]
        return ["other-model"]

    monkeypatch.setattr(auth, "fetch_models", fake_fetch)
    res = CliRunner().invoke(app, ["doctor", "--fix"])
    assert res.exit_code == 0, res.output
    assert pulled == ["qwen2.5-coder:7b"]
    assert "installed" in res.output


def test_doctor_no_fix_only_suggests(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from sk.cli import app

    import sk.auth as auth
    import sk.init_wizard as wizard

    monkeypatch.setattr(auth, "provider_status", lambda cfg: (True, "ok"))
    monkeypatch.setattr(auth, "fetch_models", lambda *a, **k: ["other-model"])
    pulled: list[str] = []
    monkeypatch.setattr(wizard, "pull_model", lambda name, **k: pulled.append(name) or (True, "x"))
    res = CliRunner().invoke(app, ["doctor"])
    assert res.exit_code == 0, res.output
    assert pulled == [] and "ollama pull" in res.output


def test_report_redacts_keys(tmp_path, monkeypatch):
    import sk.config as config_mod
    import sk.store as store

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    cfg = _cfg(api_key="sk-super-secret-key-12345")
    text = system.build_report(cfg, (True, "valid (3 models listed)"))
    assert "sk-super-secret-key-12345" not in text
    key_lines = [ln for ln in text.splitlines() if ln.startswith("- api_key:")]
    assert len(key_lines) == 1 and Config.mask("sk-super-secret-key-12345") in key_lines[0]
    assert "provider: ollama" in text and "valid (3 models listed)" in text


def test_report_includes_errors_tail(tmp_path, monkeypatch):
    import sk.config as config_mod
    import sk.store as store

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    (tmp_path / ".sidekick").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".sidekick" / "tui-errors.log").write_text("[t] approve: x = denied\n" * 20)
    text = system.build_report(_cfg(), (False, "down"))
    assert "recent_errors" in text and "denied" in text
    assert text.count("denied") <= 10  # capped tail


def test_report_cli_no_network(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from sk.cli import app

    import sk.auth as auth

    monkeypatch.setattr(auth, "provider_status", lambda cfg: (False, "mocked down"))
    res = CliRunner().invoke(app, ["report"])
    assert res.exit_code == 0, res.output
    assert "# sidekick report" in res.output and "mocked down" in res.output
