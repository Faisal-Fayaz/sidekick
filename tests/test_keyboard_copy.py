"""Keyboard copy-range tests (fixes #163): F6 mark/copy without a mouse.
Fully offline."""

import sk.store as store
from sk.tui import SidekickTUI


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")


def _blob(app) -> str:
    return "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)


async def _seed(app, pilot, n=40):
    from sk.tui.helpers import _role

    log = app.query_one("#chat-log")
    for i in range(n):
        _role(log, "you", f"rangeline {i}")
    await pilot.pause()
    await pilot.pause()


async def _pilot_full_range(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    copied = []
    monkeypatch.setattr(app, "_copy_out", lambda text, what: copied.append((text, what)) or True)
    async with app.run_test(size=(100, 40)) as pilot:
        await _seed(app, pilot)
        app._scroll_log("top")
        await pilot.pause()
        await pilot.press("f6")
        await pilot.pause()
        assert app._mark_row == 0
        assert "marked line 0" in _blob(app)
        app._scroll_log("bottom")
        await pilot.pause()
        await pilot.press("f6")
        await pilot.pause()
        assert len(copied) == 1
        text, what = copied[0]
        assert "rangeline 0" in text and "rangeline 39" in text
        assert "copied lines" in _blob(app)


async def _pilot_middle_range(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    copied = []
    monkeypatch.setattr(app, "_copy_out", lambda text, what: copied.append(text) or True)
    async with app.run_test(size=(100, 40)) as pilot:
        await _seed(app, pilot, n=120)
        log = app.query_one("#chat-log")
        assert log.max_scroll_y > 30  # genuinely scrollable
        log.scroll_to(y=10, animate=False)
        await pilot.pause()
        await pilot.press("f6")
        await pilot.pause()
        log.scroll_to(y=19, animate=False)
        await pilot.pause()
        await pilot.press("f6")
        await pilot.pause()
        assert len(copied) == 1
        assert "rangeline 1" in copied[0]  # middle span (10-19ish)
        assert "rangeline 0" not in copied[0] and "rangeline 119" not in copied[0]
        assert "copied lines" in _blob(app)
        # reversed direction covers the identical span
        log.scroll_to(y=19, animate=False)
        await pilot.pause()
        await pilot.press("f6")
        await pilot.pause()
        log.scroll_to(y=10, animate=False)
        await pilot.pause()
        await pilot.press("f6")
        await pilot.pause()
        assert len(copied) == 2
        assert copied[1] == copied[0]


async def _pilot_empty_log(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.query_one("#chat-log").clear()
        await pilot.pause()
        await pilot.press("f6")
        await pilot.pause()
        assert "nothing to copy yet" in _blob(app)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_pilot_full_range(monkeypatch, tmp_path):
    _run(_pilot_full_range(monkeypatch, tmp_path))


def test_pilot_middle_range(monkeypatch, tmp_path):
    _run(_pilot_middle_range(monkeypatch, tmp_path))


def test_pilot_empty_log(monkeypatch, tmp_path):
    _run(_pilot_empty_log(monkeypatch, tmp_path))
