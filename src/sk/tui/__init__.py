"""TUI package: chat UI (split from sk/tui.py, pure move).

All names below preserve the old `sk.tui.X` surface (tests patch
`sk.tui._role` / `sk.tui._w`; `sk.cli` imports `launch`).
"""

from .app import SidekickTUI
from .helpers import (
    _line,
    _load_history,
    _now,
    _role,
    _rule,
    _save_history,
    _w,
    is_affirmative,
    log_error,
)
from .launch import launch
from .theme import (
    current_name,
    install_sidekick_theme,
    mode_for_name,
    name_for_mode,
    set_theme,
    toggle_theme,
)
from .widgets import ChatArea, ChatLog

__all__ = [
    "SidekickTUI",
    "ChatArea",
    "ChatLog",
    "launch",
    "install_sidekick_theme",
    "set_theme",
    "toggle_theme",
    "current_name",
    "name_for_mode",
    "mode_for_name",
    "is_affirmative",
    "log_error",
    "_line",
    "_load_history",
    "_now",
    "_role",
    "_rule",
    "_save_history",
    "_w",
]
