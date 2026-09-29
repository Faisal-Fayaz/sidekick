"""sk config --theme tests (#177): set/persist all 5 themes, aliases, invalid. Offline."""

from typer.testing import CliRunner

from sk.cli import app


def _iso(monkeypatch, tmp_path):
    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")


def test_config_theme_sets_all_five(monkeypatch, tmp_path):
    import sk.config as config_mod

    _iso(monkeypatch, tmp_path)
    for name in ("sidekick", "sidekick-light", "opencode", "dracula", "tokyonight"):
        res = CliRunner().invoke(app, ["config", "--theme", name])
        assert res.exit_code == 0, res.output
        assert name in res.output
        assert config_mod.Config.load().theme == name  # persisted


def test_config_theme_legacy_and_case(monkeypatch, tmp_path):
    import sk.config as config_mod

    _iso(monkeypatch, tmp_path)
    res = CliRunner().invoke(app, ["config", "--theme", "dark"])
    assert res.exit_code == 0, res.output
    assert config_mod.Config.load().theme == "sidekick"
    res = CliRunner().invoke(app, ["config", "--theme", "LIGHT"])
    assert res.exit_code == 0, res.output
    assert config_mod.Config.load().theme == "sidekick-light"
    res = CliRunner().invoke(app, ["config", "--theme", "  Dracula  "])
    assert res.exit_code == 0, res.output
    assert config_mod.Config.load().theme == "dracula"


def test_config_theme_invalid_lists_valid_no_save(monkeypatch, tmp_path):
    import sk.config as config_mod

    _iso(monkeypatch, tmp_path)
    res = CliRunner().invoke(app, ["config", "--theme", "neon"])
    assert res.exit_code == 1, res.output
    for name in ("sidekick", "sidekick-light", "opencode", "dracula", "tokyonight"):
        assert name in res.output  # valid list shown
    assert config_mod.Config.load().theme == "sidekick"  # default kept, neon rejected


def test_config_show_includes_theme(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    res = CliRunner().invoke(app, ["config", "--theme", "dracula"])
    assert res.exit_code == 0, res.output
    res = CliRunner().invoke(app, ["config", "--show"])
    assert res.exit_code == 0, res.output
    assert "theme=dracula" in res.output
