"""Capture real TUI screenshots headless for the README. No LLM, no network.

Deterministic by design: the capture runs against a throwaway HOME, seeds a few
demo sessions, and clears the startup log so the images never leak the person
running the script. The model shown is a display default; no model is called.

Usage:
    uv run python docs/shot.py
    SK_SHOT_THEME=light uv run python docs/shot.py
    SIDEKICK_MODEL=qwen3-coder:30b uv run python docs/shot.py
"""

import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path

# Set HOME before importing anything that resolves ~/.sidekick at module import.
_SHOT_HOME = Path(tempfile.mkdtemp(prefix="sidekick-shot-"))
os.environ["HOME"] = str(_SHOT_HOME)
os.environ.setdefault("SIDEKICK_PROVIDER", "ollama")
os.environ.setdefault("SIDEKICK_MODEL", "qwen3-coder:30b")

from sk.store import save_message  # noqa: E402
from sk.tui import SidekickTUI, _role  # noqa: E402

THEME = os.getenv("SK_SHOT_THEME", "")
SUFFIX = "-light" if THEME == "light" else ""
SESSION = "demo-review"

DEMO_SESSIONS = {
    "demo-refactor": [
        ("user", "extract the retry guard into a helper"),
        ("assistant", "I'll move the check out of the loop and add a unit test."),
    ],
    "demo-tests": [
        ("user", "why is test_run_json failing on 3.12?"),
        ("assistant", "The fixture assumes the old error envelope shape."),
    ],
    "demo-brief": [
        ("user", "morning brief: what changed overnight?"),
        ("assistant", "Two CI failures, one dirty worktree, and a stale branch."),
    ],
    SESSION: [
        ("user", "review the retry changes in src/sk/auth.py"),
        ("assistant", "Found one blocker and one missing test."),
    ],
}


def seed_sessions() -> None:
    """Give the sessions drawer clean, deterministic content."""
    for session, messages in DEMO_SESSIONS.items():
        for role, text in messages:
            save_message(session, role, text)
        time.sleep(0.02)  # keep list_sessions' MAX(ts) ordering stable


def _write_intro(log) -> None:
    log.clear()
    log.write("sidekick online. Enter sends · ctrl+j newline · ctrl+g to talk · F3 sessions")


def _write_review(log) -> None:
    _role(log, "you", "review the retry changes in src/sk/auth.py")
    _role(log, "tool", "○ tool: read_file {'path': 'src/sk/auth.py'}")
    _role(log, "sidekick", "")
    log.write("Found one blocker. _retry_after trusts the Retry-After header without a ceiling,")
    log.write("so a hostile upstream can park the agent for hours.")
    log.write("")
    log.write("Fix: clamp it to MAX_RETRY_AFTER and add a regression test for a")
    log.write("Retry-After: 999999 response.")


def _write_sessions_demo(log) -> None:
    """Short enough to fit beside the 44-column drawer without clipping."""
    log.clear()
    log.write("sidekick online · F3 sessions")
    _role(log, "you", "review src/sk/auth.py")
    _role(log, "tool", "○ read_file")
    _role(log, "sidekick", "")
    log.write("Found one blocker: clamp Retry-After.")


async def main() -> None:
    app = SidekickTUI(session=SESSION)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()  # let on_mount register the themes before switching
        app._sub()  # this also applies the configured theme
        if THEME == "light":
            from sk.tui.theme import LIGHT_NAME, install_sidekick_theme

            install_sidekick_theme(app, LIGHT_NAME)
            app.refresh(repaint=True, layout=True)
            app.screen.refresh(repaint=True, layout=True)
            await pilot.pause()
        log = app.query_one("#chat-log")
        _write_intro(log)
        _write_review(log)
        await pilot.pause()
        await pilot.pause()

        area = app.query_one("#chat-input")
        area.focus()
        area.text = "/rev"
        await pilot.pause()
        await pilot.pause()
        app.save_screenshot(f"docs/tui-complete{SUFFIX}.svg")
        print("shot 1: complete popup")

        area.text = ""
        await pilot.pause()
        app.save_screenshot(f"docs/tui-chat{SUFFIX}.svg")
        print("shot 2: conversation")

        app.action_toggle_sessions()
        await pilot.pause()
        _write_sessions_demo(log)
        await pilot.pause()
        app.save_screenshot(f"docs/tui-sessions{SUFFIX}.svg")
        print("shot 3: sessions drawer")


try:
    seed_sessions()
    asyncio.run(main())
finally:
    shutil.rmtree(_SHOT_HOME, ignore_errors=True)
