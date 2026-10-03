"""Resolved approval-card tests (fixes #166): every outcome leaves a
✓/✗ marker; late answers stay denied. Fully offline."""

import threading
import time as _t

import sk.store as store
from sk.tui import SidekickTUI


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")


def _approval_fake(calls):
    def fake(
        text,
        hist,
        cfg,
        on_tool=None,
        on_token=None,
        approve=None,
        on_reasoning=None,
        auto_approve=False,
        review_plan=None,
        read_only=False,
        plan_mode=False,
        cancel=None,
    ):
        ok = approve("write_file", {"path": "/tmp/x", "content": "hi"})
        calls.append(ok)
        return "wrote it" if ok else "blocked"

    return fake


def _blob(app) -> str:
    return "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)


async def _drive(monkeypatch, tmp_path, answer, timeout_s=None):
    import sk.agent as agent

    _iso(tmp_path, monkeypatch)
    calls: list[bool] = []
    monkeypatch.setattr(agent, "run_agent", _approval_fake(calls))
    app = SidekickTUI()
    if timeout_s is not None:
        app._approve_timeout = timeout_s
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input")
        area.focus()
        area.text = "write something"
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            if "allow write_file" in _blob(app):
                break
        assert "allow write_file" in _blob(app)
        if answer is not None:
            area.focus()
            area.text = answer
            await pilot.pause()
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause()
                if "blocked" in _blob(app) or "wrote it" in _blob(app):
                    break
        else:
            for _ in range(60):
                await pilot.pause()
                if "timed out" in _blob(app):
                    break
        return _blob(app), calls


async def _pilot_approved_marker(monkeypatch, tmp_path):
    blob, calls = await _drive(monkeypatch, tmp_path, "y")
    assert calls == [True]
    assert "✓ approved write_file -> /tmp/x" in blob


async def _pilot_denied_marker(monkeypatch, tmp_path):
    blob, calls = await _drive(monkeypatch, tmp_path, "n")
    assert calls == [False]
    assert "✗ denied write_file -> /tmp/x (you answered 'n')" in blob


async def _pilot_timeout_marker(monkeypatch, tmp_path):
    blob, calls = await _drive(monkeypatch, tmp_path, None, timeout_s=1)
    assert calls == [False]
    assert "✗ denied write_file -> /tmp/x" in blob and "timed out" in blob


async def _pilot_late_answer_stays_denied(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        event = threading.Event()
        app._pending_approval = {
            "question": "write_file -> /tmp/z",
            "event": event,
            "answer": False,
            "asked_at": _t.monotonic() - 999,
            "timeout": 300,
            "reply": "",
            "token": object(),
            "owner": threading.get_ident(),  # live: slot present but expired
            "deadline": _t.monotonic() + 300,
        }
        assert app._answer_pending("y") == "too-late"
        # The denial stands — `answer` stays False — but the waiter is now woken.
        # This asserted `not event.is_set()` with the comment "worker never woken",
        # which is the defect #311 describes: the blocked turn thread stayed in
        # event.wait() for the full timeout (up to five minutes) and accumulated.
        # Waking it cannot approve anything, because the verdict was already False.
        assert event.is_set(), "the late-answer path must release the blocked worker"
        assert app._pending_approval is None
        await pilot.pause()
        assert "too late — already denied" in _blob(app)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_pilot_approved_marker(monkeypatch, tmp_path):
    _run(_pilot_approved_marker(monkeypatch, tmp_path))


def test_pilot_denied_marker(monkeypatch, tmp_path):
    _run(_pilot_denied_marker(monkeypatch, tmp_path))


def test_pilot_timeout_marker(monkeypatch, tmp_path):
    _run(_pilot_timeout_marker(monkeypatch, tmp_path))


def test_pilot_late_answer_stays_denied(monkeypatch, tmp_path):
    _run(_pilot_late_answer_stays_denied(monkeypatch, tmp_path))
