"""Collapsible output tests (fixes #168): long answers fold with a summary,
F7 unfolds in a panel, Esc dismisses. Fully offline."""

import sk.store as store
from sk.tui import SidekickTUI


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")


def _blob(app) -> str:
    return "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)


def _panel_blob(app) -> str:
    return "\n".join(str(ln) for ln in app.query_one("#fold-panel").lines)


async def _pilot_fold_unfold(monkeypatch, tmp_path):
    from textual.widgets import RichLog

    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        long_answer = "\n".join(f"body line {i}" for i in range(20))
        app._finish(long_answer, "1s")
        await pilot.pause()
        blob = _blob(app)
        assert "▸ answer #1 (20 lines)" in blob
        assert "body line 19" not in blob
        assert len(app._folded) == 1
        await pilot.press("f7")
        await pilot.pause()
        panel = app.query_one("#fold-panel", RichLog)
        assert panel.display
        assert "body line 19" in _panel_blob(app)
        await pilot.press("f7")
        await pilot.pause()
        assert not app.query_one("#fold-panel", RichLog).display


async def _pilot_short_and_boundary(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app._finish("\n".join(f"short {i}" for i in range(5)), "1s")
        await pilot.pause()
        assert "short 4" in _blob(app) and not app._folded
        app._finish("\n".join(f"edge {i}" for i in range(12)), "1s")
        await pilot.pause()
        assert "edge 11" in _blob(app) and not app._folded
        app._finish("\n".join(f"over {i}" for i in range(13)), "1s")
        await pilot.pause()
        assert "▸ answer #1 (13 lines)" in _blob(app)
        assert "over 12" not in _blob(app)


async def _pilot_esc_and_switch(monkeypatch, tmp_path):
    from textual.widgets import RichLog

    _iso(tmp_path, monkeypatch)
    store.save_message("s1", "user", "hi")
    app = SidekickTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app._finish("\n".join(f"long {i}" for i in range(15)), "1s")
        await pilot.pause()
        await pilot.press("f7")
        await pilot.pause()
        assert app.query_one("#fold-panel", RichLog).display
        await pilot.press("escape")
        await pilot.pause()
        assert not app.query_one("#fold-panel", RichLog).display
        app._show_session("s1", "")
        await pilot.pause()
        assert app._folded == []


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_pilot_fold_unfold(monkeypatch, tmp_path):
    _run(_pilot_fold_unfold(monkeypatch, tmp_path))


def test_pilot_short_and_boundary(monkeypatch, tmp_path):
    _run(_pilot_short_and_boundary(monkeypatch, tmp_path))


def test_pilot_esc_and_switch(monkeypatch, tmp_path):
    _run(_pilot_esc_and_switch(monkeypatch, tmp_path))
