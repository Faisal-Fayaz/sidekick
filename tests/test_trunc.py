"""Session-tail honesty tests (fixes #164): markers iff truncation applied.
Fully offline."""

import sk.store as store
from sk.tui import SidekickTUI
from sk.tui.app import TAIL_CHARS, TAIL_MESSAGES, _tail_slice, _tail_text


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")


def _seed(session, n, size=10):
    for i in range(n):
        store.save_message(session, "user" if i % 2 == 0 else "assistant", f"m{i} " + "x" * size)


def test_tail_slice():
    assert _tail_slice([]) == (False, [])
    assert _tail_slice([{"a": 1}] * TAIL_MESSAGES)[0] is False
    truncated, tail = _tail_slice([{"a": i} for i in range(TAIL_MESSAGES + 2)])
    assert truncated is True and [m["a"] for m in tail] == [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]


def test_tail_text():
    assert _tail_text("hi") == "hi"
    assert _tail_text("") == ""
    assert _tail_text("y" * TAIL_CHARS) == "y" * TAIL_CHARS
    out = _tail_text("y" * (TAIL_CHARS + 44))
    assert out.startswith("y" * TAIL_CHARS) and "[+44 chars hidden]" in out


def _blob(app) -> str:
    return "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)


async def _pilot_markers(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    _seed("long", TAIL_MESSAGES + 2)
    _seed("short", 3)
    store.save_message("long", "user", "z" * (TAIL_CHARS + 7))
    app = SidekickTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._show_session("long", "")
        await pilot.pause()
        blob = _blob(app)
        assert f"showing last {TAIL_MESSAGES} of {TAIL_MESSAGES + 3} messages" in blob
        assert "[+7 chars hidden]" in blob
        app._show_session("short", "")
        await pilot.pause()
        blob = _blob(app)
        assert "showing last" not in blob and "chars hidden" not in blob


async def _pilot_mount_markers(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    _seed("resume-me", TAIL_MESSAGES + 1)
    app = SidekickTUI(session="resume-me")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        blob = _blob(app)
        assert "continued `resume-me`" in blob
        assert f"showing last {TAIL_MESSAGES} of {TAIL_MESSAGES + 1} messages" in blob


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_pilot_markers(monkeypatch, tmp_path):
    _run(_pilot_markers(monkeypatch, tmp_path))


def test_pilot_mount_markers(monkeypatch, tmp_path):
    _run(_pilot_mount_markers(monkeypatch, tmp_path))
