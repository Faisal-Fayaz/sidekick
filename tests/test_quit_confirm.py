"""Quit-confirmation tests (fixes #167): quitting with a recording or a
pending approval arms instead of silently denying + exiting. Fully offline."""

import threading
import time as _t

import sk.store as store
from sk.tui import SidekickTUI


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")


def _live_slot():
    return {
        "question": "write_file -> /tmp/z",
        "event": threading.Event(),
        "answer": False,
        "asked_at": _t.monotonic(),
        "reply": "",
        "token": object(),
        "owner": threading.get_ident(),
        "deadline": _t.monotonic() + 300,
    }


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


async def _pilot_approval_arms_then_quits(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    async with app.run_test() as pilot:
        app._pending_approval = _live_slot()
        await _send(app, pilot, "/quit")
        assert app._quit_armed is True
        assert app._pending_approval is not None  # not silently dropped
        assert "approval waiting for an answer" in _blob(app)
        await _send(app, pilot, "/quit")
        assert app._pending_approval is None  # second quit releases + exits


async def _pilot_recording_arms_and_abandons(monkeypatch, tmp_path):
    import sk.voice as voice_mod

    _iso(tmp_path, monkeypatch)
    stopped = []
    monkeypatch.setattr(
        voice_mod, "stop_recording", lambda proc, wav_path="": stopped.append(proc) or ""
    )
    app = SidekickTUI()
    async with app.run_test() as pilot:
        app._rec_proc = object()
        await _send(app, pilot, "/quit")
        assert app._quit_armed is True
        assert app._rec_proc is not None
        assert "recording in progress" in _blob(app)
        await _send(app, pilot, "/quit")
        assert stopped and app._rec_proc is None


async def _pilot_other_input_disarms(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    async with app.run_test() as pilot:
        app._pending_approval = _live_slot()
        await _send(app, pilot, "/quit")
        assert app._quit_armed is True
        await _send(app, pilot, "/help")
        assert app._quit_armed is False
        assert app._pending_approval is not None  # help doesn't answer cards


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_pilot_approval_arms_then_quits(monkeypatch, tmp_path):
    _run(_pilot_approval_arms_then_quits(monkeypatch, tmp_path))


def test_pilot_recording_arms_and_abandons(monkeypatch, tmp_path):
    _run(_pilot_recording_arms_and_abandons(monkeypatch, tmp_path))


def test_pilot_other_input_disarms(monkeypatch, tmp_path):
    _run(_pilot_other_input_disarms(monkeypatch, tmp_path))
