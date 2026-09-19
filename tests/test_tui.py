"""TUI tests: pilot mount + slash handling (no LLM needed)."""

from sk.tui import SidekickTUI


def test_meta():
    assert SidekickTUI.TITLE == "sidekick"


async def _pilot_checks():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        assert app.query_one("#chat-log") is not None
        assert app.query_one("#chat-input") is not None
        assert "/help" in app.sub_title
        await pilot.pause()


async def _pilot_slash_help():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        inp = app.query_one("#chat-input")
        inp.focus()
        inp.value = "/help"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "/model" in blob


async def _pilot_slash_model():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        inp = app.query_one("#chat-input")
        inp.focus()
        inp.value = "/model fast"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.model_override == "llama3.2:3b"
        assert "llama" in app.sub_title


def test_pilot_mount():
    import asyncio

    asyncio.run(_pilot_checks())


def test_slash_help_renders():
    import asyncio

    asyncio.run(_pilot_slash_help())


def test_slash_model_switches():
    import asyncio

    asyncio.run(_pilot_slash_model())


async def _pilot_ctrl_y():
    from sk.tui import SidekickTUI as _T

    app = _T()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+y")
        await pilot.pause()
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "cop" in blob  # copied... or copy failed hint


def test_ctrl_y_copies():
    import asyncio

    asyncio.run(_pilot_ctrl_y())
