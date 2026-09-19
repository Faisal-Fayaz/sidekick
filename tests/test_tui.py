"""TUI tests: pilot mount (needs no real terminal)."""

from sk.tui import SidekickTUI


def test_meta():
    assert SidekickTUI.TITLE == "sidekick"


async def _pilot_checks():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        assert app.query_one("#todo-table") is not None
        assert app.query_one("#mem-table") is not None
        assert app.query_one("#chat-input") is not None
        assert app.query_one("#brief-log") is not None
        await pilot.pause()


async def _pilot_chat_feedback():
    """Sending chat shows immediate feedback (no LLM wait)."""
    from textual.widgets import TabbedContent

    app = SidekickTUI()
    async with app.run_test() as pilot:
        app.query_one(TabbedContent).active = "chat"
        await pilot.pause()
        inp = app.query_one("#chat-input")
        inp.focus()
        inp.value = "hello test"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "hello test" in blob and "thinking" in blob


def test_pilot_mount():
    import asyncio

    asyncio.run(_pilot_checks())


def test_chat_immediate_feedback():
    import asyncio

    asyncio.run(_pilot_chat_feedback())
