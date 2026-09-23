"""Capture real TUI screenshots headless for the README. No LLM, no network."""

import asyncio
import os

from sk.tui import SidekickTUI

THEME = os.getenv("SK_SHOT_THEME", "")
SUFFIX = "-light" if THEME == "light" else ""


async def main() -> None:
    # 1. chat view with content + slash popup open
    app = SidekickTUI()
    async with app.run_test(size=(100, 30)) as pilot:
        if THEME == "light":
            from sk.tui import set_theme
            from sk.tui.theme import LIGHT_NAME

            set_theme(app, LIGHT_NAME)
            await pilot.pause()
        log = app.query_one("#chat-log")
        log.write("sidekick online. Enter sends · ctrl+j newline · ↑ history · ctrl+g to talk.")
        from sk.tui import _role

        _role(log, "you", "what files are in ~/sidekick?")
        _role(log, "tool", "○ tool: list_dir {'path': '~/sidekick'}")
        _role(log, "sidekick", "")
        log.write("**~/sidekick**: `pyproject.toml`, `src/sk/`, `tests/`, `README.md`")
        area = app.query_one("#chat-input")
        area.focus()
        area.text = "/mo"
        await pilot.pause()
        await pilot.pause()
        app.save_screenshot(f"docs/tui-complete{SUFFIX}.svg")
        print("shot 1: complete popup")

        # 2. rich conversation: tools + answer + stats line
        area.text = ""
        await pilot.pause()
        from sk.tui import _role

        _role(log, "you", "summarize disk usage in ~/")
        _role(log, "tool", "○ tool: exec {'cmd': 'df -h'}")
        _role(log, "sidekick", "")
        log.write("/dev/nvme0n1p8  133G  117G  8.5G  94% /")
        await pilot.pause()
        await pilot.pause()
        app.save_screenshot(f"docs/tui-chat{SUFFIX}.svg")
        print("shot 2: conversation")


asyncio.run(main())
