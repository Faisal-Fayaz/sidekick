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


def test_pilot_mount():
    import asyncio

    asyncio.run(_pilot_checks())
