"""Capture real TUI screenshots headless for the README. No LLM, no network.

The screenshots are curated demo sessions, not a recording of a live model
call. The TUI itself is captured headless at a fixed size, against a throwaway
HOME, so the images are deterministic and never leak the person running the
script.

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

from rich.markdown import Markdown  # noqa: E402

from sk.store import save_message  # noqa: E402
from sk.tui import SidekickTUI, _role  # noqa: E402
from sk.tui import helpers as _tui_helpers  # noqa: E402

# The log stamps every line with the wall clock. Freeze it so re-running the
# script changes the capture only when the demo content changes.
_tui_helpers._now = lambda: "09:41"  # noqa: E731

THEME = os.getenv("SK_SHOT_THEME", "")
SUFFIX = "-light" if THEME == "light" else ""
SIZE = (100, 30)

DEMO_SESSIONS = {
    "demo-tests": [
        ("user", "why is test_run_json failing?"),
        ("assistant", "The fixture assumes the old error shape."),
    ],
    "demo-refactor": [
        ("user", "extract the retry guard"),
        ("assistant", "I'll move the check out of the loop and add a unit test."),
    ],
    "demo-brief": [
        ("user", "morning brief"),
        ("assistant", "Two CI failures, one dirty worktree, and a stale branch."),
    ],
    "demo-review": [
        ("user", "review working-tree diff"),
        ("assistant", "One test gap and one diff to verify."),
    ],
    "demo-fix": [
        ("user", "add Retry-After cap test"),
        ("assistant", "Added the regression; production already clamps."),
    ],
}


def seed_sessions() -> None:
    """Give the sessions drawer clean, deterministic content."""
    for session, messages in DEMO_SESSIONS.items():
        for role, text in messages:
            save_message(session, role, text)
        time.sleep(0.02)  # keep list_sessions' MAX(ts) ordering stable


def _write(log, text: str, role: str = "") -> None:
    _role(log, role, "")
    log.write(Markdown(text))


def _start(log) -> None:
    log.clear()
    log.write("sidekick online · Enter sends · ctrl+j newline · F3 sessions")


def _write_fix(log, app: SidekickTUI) -> None:
    """A complete write-and-verify turn, including the plan approval card."""
    log.clear()
    _role(log, "you", "add the Retry-After cap test and run it")
    _role(log, "sidekick", "I'll add one regression and run it; `_post` already clamps.")
    # This is the same panel `_review_plan` shows for a multi-tool turn.
    app._ask_approval(
        "plan",
        "2 tools",
        "1. write_file -> tests/test_retry_after.py\n"
        "2. shell -> pytest -q tests/test_retry_after.py",
    )
    _role(log, "you", "y")
    _role(log, "sys", "approved: plan -> 2 tools")
    _role(log, "tool", "○ tool: write_file {'path': 'tests/test_retry_after.py'}")
    _role(log, "tool", "○ tool: shell {'cmd': 'pytest -q tests/test_retry_after.py'}")
    _write(
        log,
        "Added `tests/test_retry_after.py`. It returns a 429 with "
        "`Retry-After: 999999` and asserts the retry sleeps 30s, not 999999s.\n\n"
        "```python\n"
        "assert sleeps == [30]   # not 999999\n"
        "```\n\n"
        "`pytest -q tests/test_retry_after.py` → **1 passed in 0.31s**",
        "sidekick",
    )


def _write_review(log) -> None:
    """A review of the diff produced by the fix turn above."""
    _start(log)
    _role(log, "you", "/review")
    _write(
        log,
        "Reviewed **1 file (+18 −0)** against `HEAD`.\n\n"
        "**MAJOR** `tests/test_retry_after.py:14` — only `429` is covered; "
        "`_post()` also retries `529`.\n\n"
        "```diff\n"
        '-@pytest.mark.parametrize("status", [429])\n'
        '+@pytest.mark.parametrize("status", [429, 529])\n'
        "```\n\n"
        "**Verdict: needs work** — parametrize both retry statuses, then ship.",
        "sidekick",
    )


def _write_sessions_demo(log) -> None:
    """Short enough to fit beside the 44-column drawer without clipping."""
    log.clear()
    log.write("sidekick online · F3 sessions")
    _role(log, "you", "/review")
    _write(
        log,
        "Reviewed **1 file (+18 −0)** against `HEAD`.\n\n"
        "**MAJOR** `tests/test_retry_after.py:14` — add `529`.",
        "sidekick",
    )


async def _apply_light_theme(app, pilot) -> None:
    if THEME != "light":
        return
    from sk.tui.theme import LIGHT_NAME, install_sidekick_theme

    install_sidekick_theme(app, LIGHT_NAME)
    app.refresh(repaint=True, layout=True)
    app.screen.refresh(repaint=True, layout=True)
    await pilot.pause()


async def main() -> None:
    app = SidekickTUI(session="demo-fix")
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()  # let on_mount register the themes before switching
        app._sub()  # this also applies the configured theme
        await _apply_light_theme(app, pilot)
        log = app.query_one("#chat-log")

        _write_fix(log, app)
        await pilot.pause()
        await pilot.pause()
        app.save_screenshot(f"docs/tui-chat{SUFFIX}.svg")
        print("shot 1: write + verify")

        app.session = "demo-review"
        app._sub()
        await _apply_light_theme(app, pilot)
        _write_review(log)
        area = app.query_one("#chat-input")
        area.focus()
        area.text = "/rev"
        await pilot.pause()
        await pilot.pause()
        app.save_screenshot(f"docs/tui-complete{SUFFIX}.svg")
        print("shot 2: review + autocomplete")

        area.text = ""
        await pilot.pause()
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
