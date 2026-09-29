"""View-undo tests (fixes #165): switch/clear stashes the visible log,
/unwipe restores it (view-only). Fully offline."""

import sk.store as store
from sk.tui import SidekickTUI


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")


def _blob(app) -> str:
    return "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)


async def _send(app, pilot, text):
    area = app.query_one("#chat-input")
    area.focus()
    area.text = text
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()
    await pilot.pause()


async def _pilot_switch_unwipe(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    store.save_message("sess-a", "user", "marker-alpha")
    store.save_message("sess-b", "user", "marker-beta")
    app = SidekickTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._show_session("sess-a", "")
        await pilot.pause()
        assert "marker-alpha" in _blob(app)
        app._show_session("sess-b", "")
        await pilot.pause()
        assert "marker-alpha" not in _blob(app)
        assert "marker-beta" in _blob(app)
        await _send(app, pilot, "/unwipe")
        blob = _blob(app)
        assert "marker-alpha" in blob and "view restored" in blob
        assert app.session == "sess-b"  # view-only: session stays put


async def _pilot_clear_unwipe(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    store.save_message("sess-a", "user", "marker-gamma")
    app = SidekickTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._show_session("sess-a", "")
        await pilot.pause()
        assert "marker-gamma" in _blob(app)
        await _send(app, pilot, "/clear")
        assert "marker-gamma" not in _blob(app)
        await _send(app, pilot, "/unwipe")
        assert "marker-gamma" in _blob(app)


async def _pilot_unwipe_empty(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        await _send(app, pilot, "/unwipe")
        assert "nothing to restore" in _blob(app)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_pilot_switch_unwipe(monkeypatch, tmp_path):
    _run(_pilot_switch_unwipe(monkeypatch, tmp_path))


def test_pilot_clear_unwipe(monkeypatch, tmp_path):
    _run(_pilot_clear_unwipe(monkeypatch, tmp_path))


def test_pilot_unwipe_empty(monkeypatch, tmp_path):
    _run(_pilot_unwipe_empty(monkeypatch, tmp_path))
