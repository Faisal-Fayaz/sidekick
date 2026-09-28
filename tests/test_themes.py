"""Theme pack tests (fixes #138): registry, normalize, config, F2 picker.
Pilot tests restore the default theme (module-global state)."""

import sk.store as store
from sk.tui import SidekickTUI
from sk.tui import theme as theme_mod


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")


def test_registry_has_five_distinct_themes():
    assert theme_mod.THEME_NAMES == (
        "sidekick",
        "sidekick-light",
        "opencode",
        "dracula",
        "tokyonight",
    )
    bgs = set()
    for name in theme_mod.THEME_NAMES:
        palette, roles = theme_mod._THEMES[name]
        assert set(roles) == {"you", "sidekick", "tool", "sys", "warn", "error"}
        for key in ("primary", "background", "error"):
            assert palette[key].startswith("#")
        bgs.add(palette["background"])
    assert len(bgs) == 5
    assert theme_mod.DARK["primary"] == "#34f5a2"  # legacy palettes untouched
    assert theme_mod.LIGHT["primary"] == "#0a7d4f"


def test_opencode_palette_matches_upstream():
    assert theme_mod.OPENCODE["primary"] == "#fab283"
    assert theme_mod.OPENCODE["background"] == "#0a0a0a"
    assert theme_mod.OPENCODE["surface"] == "#141414"
    assert theme_mod.OPENCODE_ROLES["you"] == "bold #fab283"


def test_normalize_theme_name():
    n = theme_mod.normalize_theme_name
    assert n("opencode") == "opencode"
    assert n("dracula") == "dracula"
    assert n("sidekick-light") == "sidekick-light"
    assert n("dark") == "sidekick" and n("light") == "sidekick-light"
    assert n("Dark") == "sidekick"
    assert n("bogus") == "sidekick" and n("") == "sidekick" and n(None) == "sidekick"


def test_install_registers_all_themes():
    registered = []

    class FakeApp:
        def register_theme(self, theme):
            registered.append(theme.name)

    theme_mod.install_sidekick_theme(FakeApp())
    assert registered == list(theme_mod.THEME_NAMES)


def test_config_accepts_theme_names(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    import sk.config as config_mod
    from sk.config import Config

    d = tmp_path / ".sidekick"
    d.mkdir(exist_ok=True)
    (d / "config.toml").write_text('theme = "opencode"\n')
    assert Config.load().theme == "opencode"
    (d / "config.toml").write_text('theme = "bogus"\n')
    assert Config.load().theme == "sidekick"
    (d / "config.toml").write_text('theme = "light"\n')
    assert Config.load().theme == "sidekick-light"
    assert config_mod  # keep import used


def test_theme_choices_marks_current(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    try:
        theme_mod.set_theme(SidekickTUI(), "dracula")
        app = SidekickTUI()
        rows = app._theme_choices()
        assert [t for t, _ in rows] == list(theme_mod.THEME_NAMES)
        assert rows[3] == ("dracula", "● dracula (current)")
        assert all(label.startswith("○") for _, label in rows if "current" not in label)
    finally:
        theme_mod._current = theme_mod.DARK_NAME


def test_choose_theme_bad_index_noop(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    app._drawer_themes = ["opencode"]
    app._choose_theme(99)
    assert theme_mod.current_name() == theme_mod.DARK_NAME


def _blob(app) -> str:
    return "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)


def _theme_items(app) -> list:
    from textual.widgets import ListView

    return list(app.query_one("#theme-list", ListView).children)


async def _pilot_open_select(monkeypatch, tmp_path):
    from textual.containers import Vertical

    _iso(tmp_path, monkeypatch)
    try:
        app = SidekickTUI()
        async with app.run_test() as pilot:
            await pilot.press("f2")
            await pilot.pause()
            assert app.query_one("#theme-picker", Vertical).display
            assert len(_theme_items(app)) == 5
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert theme_mod.current_name() == "opencode"
            assert "theme →" in _blob(app)
            assert not app.query_one("#theme-picker", Vertical).display
    finally:
        theme_mod._current = theme_mod.DARK_NAME


async def _pilot_escape_dismisses(monkeypatch, tmp_path):
    from textual.containers import Vertical

    _iso(tmp_path, monkeypatch)
    try:
        app = SidekickTUI()
        async with app.run_test() as pilot:
            await pilot.press("f2")
            await pilot.pause()
            assert app.query_one("#theme-picker", Vertical).display
            await pilot.press("escape")
            await pilot.pause()
            assert not app.query_one("#theme-picker", Vertical).display
            assert theme_mod.current_name() == theme_mod.DARK_NAME
    finally:
        theme_mod._current = theme_mod.DARK_NAME


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_pilot_open_select(monkeypatch, tmp_path):
    _run(_pilot_open_select(monkeypatch, tmp_path))


def test_pilot_escape_dismisses(monkeypatch, tmp_path):
    _run(_pilot_escape_dismisses(monkeypatch, tmp_path))
