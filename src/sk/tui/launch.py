"""TUI entry point (split from sk/tui.py, pure move)."""

from __future__ import annotations

from .app import SidekickTUI


def launch(
    model: str = "",
    session: str = "",
    cont: bool = False,
    allow: tuple[str, ...] = (),
) -> None:
    # Mouse tracking on: drag-select in the log auto-copies on release,
    # clicks and wheel work like every other TUI. Hold Shift to select
    # natively at terminal level.
    if cont and not session:
        from sk.store import latest_session

        session = latest_session("tui")
    SidekickTUI(model=model, session=session, allow=allow).run(mouse=True)
