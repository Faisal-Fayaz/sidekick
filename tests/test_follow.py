"""Follow-lock tests (fixes #161): scrolled-up viewport holds on writes,
new-messages pill shows, bottom re-follows. Fully offline."""

import sk.store as store
from sk.tui import SidekickTUI
from sk.tui.widgets import ChatLog


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")


def _blob(app) -> str:
    return "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)


def test_chatlog_follow_defaults():
    assert ChatLog.follow is True
    assert ChatLog.on_held is None


async def _pilot_hold_and_refollow(monkeypatch, tmp_path):
    from textual.widgets import Static

    from sk.tui.helpers import _role

    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        log = app.query_one("#chat-log")
        for i in range(60):
            _role(log, "you", f"filler line {i}")
        await pilot.pause()
        await pilot.pause()
        app._scroll_log("top")
        await pilot.pause()
        await pilot.pause()
        assert log.scroll_y < 1.0
        for i in range(3):
            _role(log, "you", f"fresh line {i}")
        await pilot.pause()
        assert log.scroll_y < 1.0  # position held, no yank
        assert log.scroll_y < log.max_scroll_y - 5
        pill = app.query_one("#follow-pill", Static)
        assert pill.display and "3 new" in str(pill.render())
        app._scroll_log("bottom")
        await pilot.pause()
        assert not app.query_one("#follow-pill", Static).display
        _role(log, "you", "after refollow")
        await pilot.pause()
        assert log.scroll_y == log.max_scroll_y


async def _pilot_finish_respects_hold(monkeypatch, tmp_path):
    from textual.widgets import Static

    from sk.tui.helpers import _role

    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        log = app.query_one("#chat-log")
        for i in range(60):
            _role(log, "you", f"filler line {i}")
        await pilot.pause()
        await pilot.pause()
        app._scroll_log("top")
        await pilot.pause()
        await pilot.pause()
        app._finish("final answer here", "1s · ~2tok")
        await pilot.pause()
        await pilot.pause()
        assert log.scroll_y < 1.0
        assert "final answer here" in _blob(app)
        assert app.query_one("#follow-pill", Static).display


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_pilot_hold_and_refollow(monkeypatch, tmp_path):
    _run(_pilot_hold_and_refollow(monkeypatch, tmp_path))


def test_pilot_finish_respects_hold(monkeypatch, tmp_path):
    _run(_pilot_finish_respects_hold(monkeypatch, tmp_path))
