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


def _patch_config_dir(monkeypatch, tmp_path):
    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")


def test_doctor_remote_provider_failure_names_fix(monkeypatch, tmp_path):
    """Remote provider down -> key/config next step naming the provider."""
    from typer.testing import CliRunner

    from sk.cli import app

    import sk.auth as auth

    _patch_config_dir(monkeypatch, tmp_path)
    monkeypatch.setenv("SIDEKICK_PROVIDER", "groq")
    monkeypatch.setattr(
        auth, "provider_status", lambda cfg: (False, "key rejected by groq (401/403)")
    )
    res = CliRunner().invoke(app, ["doctor"])
    assert res.exit_code == 0, res.output
    assert "key rejected by groq" in res.output
    flat = " ".join(res.output.split())
    assert "sk auth status groq" in flat
    assert "sk auth add groq" in flat
    assert "sk connect" in flat


def test_doctor_ollama_unreachable_names_ollama_serve(monkeypatch, tmp_path):
    """Ollama provider down -> point at `ollama serve` explicitly."""
    from typer.testing import CliRunner

    from sk.cli import app

    import sk.auth as auth

    _patch_config_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        auth, "provider_status", lambda cfg: (False, "unreachable: connect refused")
    )
    res = CliRunner().invoke(app, ["doctor"])
    assert res.exit_code == 0, res.output
    assert "unreachable" in res.output
    assert "ollama serve" in res.output


def test_doctor_lmstudio_unreachable_names_local_server(monkeypatch, tmp_path):
    """LM Studio provider down -> point at enabling its local server."""
    from typer.testing import CliRunner

    from sk.cli import app

    import sk.auth as auth

    _patch_config_dir(monkeypatch, tmp_path)
    monkeypatch.setenv("SIDEKICK_PROVIDER", "lmstudio")
    monkeypatch.setattr(
        auth, "provider_status", lambda cfg: (False, "unreachable: connect refused")
    )
    res = CliRunner().invoke(app, ["doctor"])
    assert res.exit_code == 0, res.output
    assert "unreachable" in res.output
    assert "LM Studio" in res.output


def test_doctor_local_provider_refetch_failure_names_model_list(monkeypatch, tmp_path):
    """Provider reachable but model list fails -> point at the model list commands."""
    from typer.testing import CliRunner

    from sk.cli import app

    import sk.auth as auth

    _patch_config_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "provider_status", lambda cfg: (True, "ok"))

    def boom(*a, **k):
        raise RuntimeError("model list 500")

    monkeypatch.setattr(auth, "fetch_models", boom)
    res = CliRunner().invoke(app, ["doctor"])
    assert res.exit_code == 0, res.output
    assert "not reachable" in res.output
    assert "ollama list" in res.output


def test_doctor_fix_pull_cannot_verify_names_model_list(monkeypatch, tmp_path):
    """--fix pulled a model but re-verify fails -> actionable next step, not a bare red line."""
    from typer.testing import CliRunner

    from sk.cli import app

    import sk.auth as auth
    import sk.init_wizard as wizard

    _patch_config_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "provider_status", lambda cfg: (True, "ok"))
    monkeypatch.setattr(wizard, "pull_model", lambda name, **k: (True, "pulled"))
    calls = {"n": 0}

    def fake_fetch(*a, **k):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("verify 500")
        return ["other-model"]

    monkeypatch.setattr(auth, "fetch_models", fake_fetch)
    res = CliRunner().invoke(app, ["doctor", "--fix"])
    assert res.exit_code == 0, res.output
    assert "pulled, but cannot verify" in res.output
    assert "ollama list" in res.output


def test_doctor_fix_pull_failure_names_retry(monkeypatch, tmp_path):
    """--fix pull itself fails -> raw error plus an exact retry command, not a bare red line."""
    from typer.testing import CliRunner

    from sk.cli import app

    import sk.auth as auth
    import sk.init_wizard as wizard

    _patch_config_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(auth, "provider_status", lambda cfg: (True, "ok"))
    monkeypatch.setattr(auth, "fetch_models", lambda *a, **k: ["other-model"])
    monkeypatch.setattr(wizard, "pull_model", lambda name, **k: (False, "no space left on device"))
    res = CliRunner().invoke(app, ["doctor", "--fix"])
    assert res.exit_code == 0, res.output
    assert "no space left on device" in res.output
    flat = " ".join(res.output.split())
    assert "ollama pull" in flat and "ollama list" in flat and "sk doctor" in flat


def test_doctor_fix_backup_failure_names_mv(monkeypatch, tmp_path):
    """Unreadable config + failed backup -> exact mv-aside command plus re-run step."""
    from pathlib import Path

    from typer.testing import CliRunner

    from sk.cli import app

    _patch_config_dir(monkeypatch, tmp_path)
    monkeypatch.setenv("COLUMNS", "500")  # no Rich folding of the long mv paths
    cfg_path = tmp_path / ".sidekick" / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text('max_steps = "not-a-number"\n')  # valid TOML, crashes Config.load

    def boom(self, target):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "rename", boom)
    res = CliRunner().invoke(app, ["doctor", "--fix"])
    assert res.exit_code == 0, res.output
    assert "backup failed" in res.output
    flat = " ".join(res.output.split())
    assert "mv " in flat and ".bak" in flat and "sk doctor --fix" in flat


def test_doctor_fix_repair_failure_names_inspect(monkeypatch, tmp_path):
    """Repair itself crashes -> points at the config file plus re-run step."""
    from typer.testing import CliRunner

    from sk.cli import app

    import sk.config as config_mod

    _patch_config_dir(monkeypatch, tmp_path)
    monkeypatch.setenv("COLUMNS", "500")  # no Rich folding of the long config path
    orig = config_mod.Config.ensure_created
    calls = {"n": 0}

    def flaky_ensure(self):
        calls["n"] += 1
        if calls["n"] == 1:  # fail the repair path only; later _cfg() must proceed
            raise OSError("disk gone")
        return orig(self)

    monkeypatch.setattr(config_mod.Config, "ensure_created", flaky_ensure)
    res = CliRunner().invoke(app, ["doctor", "--fix"])
    assert res.exit_code == 0, res.output
    assert "repair failed" in res.output
    flat = " ".join(res.output.split())
    assert "config.toml" in flat and "sk doctor --fix" in flat


def test_config_numeric_warning_names_doctor_fix(monkeypatch, tmp_path, capsys):
    """Garbage-numeric stderr warning names the exact reset command."""
    import sk.config as config_mod

    _patch_config_dir(monkeypatch, tmp_path)
    cfg_path = tmp_path / ".sidekick" / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text('max_steps = "junk"\n')
    cfg = config_mod.Config.load()
    assert cfg.max_steps == config_mod.DEFAULTS["max_steps"]
    err = capsys.readouterr().err
    assert "ignoring invalid max_steps" in err
    assert "sk doctor --fix" in err
