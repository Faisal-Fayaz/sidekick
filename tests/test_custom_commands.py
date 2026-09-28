"""Custom slash commands tests (fixes #153): parse/render, precedence,
builtin wins, slash dispatch, help, autocomplete. Fully offline."""

import sk.custom_commands as cc
import sk.slash as slash
import sk.store as store


def _iso(tmp_path, monkeypatch, chdir=None):
    import sk.config as config_mod

    cfgdir = tmp_path / ".sidekick"
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfgdir)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfgdir / "config.toml")
    monkeypatch.chdir(chdir or tmp_path)
    return cfgdir


def _write(d, name, body):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body)
    return d / name


def _ctx(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    from sk.config import Config

    cfg = Config(
        provider="ollama",
        model="t",
        base_url="http://x/v1",
        api_key="x",
        max_steps=1,
        temperature=0.0,
    )
    return {"session": "test", "cfg": cfg, "state": {"yolo": False}}


def test_parse_and_render(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    _write(
        cfgdir / "commands",
        "ship.md",
        '---\ndescription: Ship it\nargument-hint: "[branch]"\n---\nReview {{args}} then merge.\n',
    )
    rows = cc.list_custom_commands()
    assert rows == [("ship", "Ship it", cfgdir / "commands" / "ship.md")]
    assert cc.render_custom_command("ship", "main") == "Review main then merge.\n"
    assert cc.render_custom_command("SHIP", "x") is not None  # case-insensitive
    assert cc.render_custom_command("nope", "x") is None


def test_dollar_arguments_and_empty_args(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    _write(cfgdir / "commands", "w.md", "Do $ARGUMENTS now.\n")
    assert cc.render_custom_command("w", "it") == "Do it now.\n"
    assert cc.render_custom_command("w", "") == "Do  now.\n"


def test_no_frontmatter_uses_first_line(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    _write(cfgdir / "commands", "x.md", "\nSummarize this.\nMore.\n")
    (name, desc, _) = cc.list_custom_commands()[0]
    assert (name, desc) == ("x", "Summarize this.")


def test_bad_names_and_oversize_skipped(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    d = cfgdir / "commands"
    _write(d, "Bad Name.md", "x")
    _write(d, ".hidden.md", "x")
    _write(d, "big.md", "y" * (cc.MAX_BODY + 1))
    _write(d, "ok.md", "fine")
    assert [n for n, _, _ in cc.list_custom_commands()] == ["ok"]
    assert cc.render_custom_command("bad name", "") is None


def test_missing_dirs_empty(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    assert cc.list_custom_commands() == []
    assert cc.custom_warnings() == []


def test_project_beats_global(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    (proj / ".sidekick" / "commands").mkdir(parents=True)
    monkeypatch.chdir(proj)
    _write(cfgdir / "commands", "d.md", "global version {{args}}")
    _write(proj / ".sidekick" / "commands", "d.md", "project version {{args}}")
    assert cc.render_custom_command("d", "go") == "project version go"


def test_builtin_collision_excluded_and_warned(tmp_path, monkeypatch):
    cfgdir = _iso(tmp_path, monkeypatch)
    _write(cfgdir / "commands", "yolo.md", "custom yolo")
    names = [n for n, _ in slash.all_commands()]
    assert names.count("yolo") == 1
    assert any("yolo" in w for w in cc.custom_warnings())


def test_slash_dispatch_and_help(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    cfgdir = tmp_path / ".sidekick"
    out = slash.handle("/planx foo", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled is True and "unknown command" in out.text
    _write(cfgdir / "commands", "myplan.md", "Plan {{args}}.\n")
    out = slash.handle("/myplan auth", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled is True and out.agent_prompt == "Plan auth.\n"
    assert out.text == ""
    text = slash.handle("/help", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    assert "/myplan" in text


def test_help_warns_collisions(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    _write(tmp_path / ".sidekick" / "commands", "clear.md", "custom clear")
    text = slash.handle("/help", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    assert "shadows builtin" in text


def test_autocomplete_includes_custom(tmp_path, monkeypatch):
    from sk.tui import SidekickTUI

    _iso(tmp_path, monkeypatch)
    cfgdir = tmp_path / ".sidekick"
    _write(cfgdir / "commands", "mydeploy.md", "Deploy {{args}}.\n")
    app = SidekickTUI()
    assert any(n == "mydeploy" for n, _ in app._slash_items("myd"))
    assert any(n == "mydeploy" for n, _ in app._slash_items("mydeploy"))


def test_unknown_still_unknown(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    out = slash.handle("/definitelynotarealcommand", session="s", cfg=c["cfg"], state=c["state"])
    assert out.handled is True and "unknown command" in out.text
