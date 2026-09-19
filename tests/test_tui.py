"""TUI tests: pilot mount + chat behaviors (no LLM needed)."""

import re

from sk.tui import ChatArea, SidekickTUI


def _blob(app) -> str:
    return "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)


async def _pilot_checks():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        assert app.query_one("#chat-log") is not None
        assert app.query_one("#chat-input", ChatArea) is not None
        assert app.query_one("#live") is not None
        assert "/help" in app.sub_title
        await pilot.pause()


async def _pilot_enter_submits(monkeypatch):
    import sk.agent as agent

    monkeypatch.setattr(agent, "run_agent", lambda *a, **k: "ok done")
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "hello test"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        blob = _blob(app)
        assert "hello test" in blob and "thinking" in blob
        assert area.text == ""


async def _pilot_multiline():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "line one"
        area.cursor_to_end()
        await pilot.pause()
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert area.text == "line one\n"
        area.clear()
        area.text = "ab"
        area.cursor_to_end()
        await pilot.pause()
        await pilot.press("alt+enter")
        await pilot.pause()
        assert area.text == "ab\n"


async def _pilot_history(tmp_path=None, monkeypatch=None):
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.cmd_history = ["first cmd", "second cmd"]
        area.focus()
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        assert area.text == "second cmd"
        await pilot.press("up")
        await pilot.pause()
        assert area.text == "first cmd"
        await pilot.press("down")
        await pilot.pause()
        assert area.text == "second cmd"


async def _pilot_slash_help():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "/help"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        blob = _blob(app)
        assert "/model" in blob and "[fast|smart|name]" in blob


async def _pilot_slash_model():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "/model fast"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.model_override == "llama3.2:3b"
        assert "llama" in app.sub_title


async def _pilot_streaming(monkeypatch):
    import sk.agent as agent

    def fake(text, hist, cfg, on_tool=None, on_token=None, approve=None, on_reasoning=None):
        if on_reasoning:
            on_reasoning("hmm ")
        for tok in ("Hello", " world"):
            if on_token:
                on_token(tok)
        return "# Hi\n\n- a"

    monkeypatch.setattr(agent, "run_agent", fake)
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "hi there friend"
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause()
            try:
                if "Hi" in _blob(app):
                    break
            except Exception:
                pass
        blob = _blob(app)
        assert "Hi" in blob
        assert re.search(r"\d+s · ~\d+tok", app.sub_title)


async def _pilot_ctrl_y():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+y")
        await pilot.pause()
        blob = _blob(app)
        assert "cop" in blob  # copied... or copy failed hint


async def _pilot_timestamps():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "/help"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        blob = _blob(app)
        assert re.search(r"\[\d\d:\d\d\]", blob)


def _run(coro):
    import asyncio

    asyncio.run(coro)


def test_pilot_mount():
    _run(_pilot_checks())


def test_enter_submits(monkeypatch):
    _run(_pilot_enter_submits(monkeypatch))


def test_ctrl_j_newline():
    _run(_pilot_multiline())


def test_history_arrows():
    _run(_pilot_history())


def test_slash_help_renders():
    _run(_pilot_slash_help())


def test_slash_model_switches():
    _run(_pilot_slash_model())


def test_streaming_and_stats(monkeypatch):
    _run(_pilot_streaming(monkeypatch))


def test_ctrl_y_copies():
    _run(_pilot_ctrl_y())


def test_timestamps():
    _run(_pilot_timestamps())
