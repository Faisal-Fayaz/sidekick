"""/theme slash tests (#187): list/switch/invalid, persistence. REPL-only, offline."""

import sk.slash as slash
import sk.store as store
from sk.config import Config
from sk.tui import theme as theme_mod


def _ctx(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    cfg = Config(
        provider="ollama",
        model="t",
        base_url="http://x/v1",
        api_key="x",
        max_steps=1,
        temperature=0.0,
    )
    return {"session": "test", "cfg": cfg, "state": {"yolo": False}}


def test_theme_lists_all_five_with_current(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    try:
        out = slash.handle("/theme", session="s", cfg=c["cfg"], state=c["state"])
        assert out.handled and not out.agent_prompt
        for name in theme_mod.THEME_NAMES:
            assert name in out.text
        assert "sidekick ← current" in out.text  # default dark normalizes to sidekick
    finally:
        theme_mod._current = theme_mod.DARK_NAME


def test_theme_switches_and_persists(tmp_path, monkeypatch):
    import sk.config as config_mod

    c = _ctx(tmp_path, monkeypatch)
    try:
        for name in theme_mod.THEME_NAMES:
            out = slash.handle("/theme", session="s", cfg=c["cfg"], state=c["state"])
            assert out.handled
            out = slash.handle(f"/theme {name}", session="s", cfg=c["cfg"], state=c["state"])
            assert out.handled and out.text == f"theme → `{name}`"
            assert config_mod.Config.load().theme == name  # persisted like F2
            assert theme_mod.current_name() == name  # live roles switched
        out = slash.handle("/theme", session="s", cfg=c["cfg"], state=c["state"])
        assert "tokyonight ← current" in out.text
    finally:
        theme_mod._current = theme_mod.DARK_NAME


def test_theme_legacy_and_case(tmp_path, monkeypatch):
    import sk.config as config_mod

    c = _ctx(tmp_path, monkeypatch)
    try:
        out = slash.handle("/theme dark", session="s", cfg=c["cfg"], state=c["state"])
        assert out.handled and out.text == "theme → `sidekick`"
        out = slash.handle("/theme LIGHT", session="s", cfg=c["cfg"], state=c["state"])
        assert out.handled and out.text == "theme → `sidekick-light`"
        assert config_mod.Config.load().theme == "sidekick-light"
    finally:
        theme_mod._current = theme_mod.DARK_NAME


def test_theme_invalid_lists_valid_no_save(tmp_path, monkeypatch):
    import sk.config as config_mod

    c = _ctx(tmp_path, monkeypatch)
    try:
        out = slash.handle("/theme neon", session="s", cfg=c["cfg"], state=c["state"])
        assert out.handled and not out.agent_prompt
        for name in theme_mod.THEME_NAMES:
            assert name in out.text
        assert config_mod.Config.load().theme == "sidekick"  # default kept
        assert theme_mod.current_name() == theme_mod.DARK_NAME  # live untouched
    finally:
        theme_mod._current = theme_mod.DARK_NAME


def test_theme_in_help(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    out = slash.handle("/help", session="s", cfg=c["cfg"], state=c["state"])
    assert "/theme" in out.text
