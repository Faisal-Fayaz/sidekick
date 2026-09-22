"""TUI theme: sidekick palette (split from sk/tui.py, pure move)."""

from __future__ import annotations


def install_sidekick_theme(app) -> None:
    """Register + activate the sidekick theme. Never raises (falls back to default)."""
    try:
        from textual.theme import Theme

        app.register_theme(
            Theme(
                name="sidekick",
                primary="#00ff9d",
                secondary="#7c3aed",
                accent="#ffb000",
                background="#0b0f0c",
                surface="#111613",
                panel="#111613",
            )
        )
        app.theme = "sidekick"
    except Exception:
        pass
