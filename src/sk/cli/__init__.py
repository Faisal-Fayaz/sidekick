"""CLI package: app + shared pieces + command registration (split from sk/cli.py).

Commands live in .commands/ (imported for side-effect registration).
All names below preserve the old `sk.cli.X` surface.
"""

from .approvers import _make_approver, _make_approver_state, _make_on_token, _make_on_tool
from .base import _cfg, app, console

# side-effect imports: register all @app.command()s
from .commands import auth, chat, daemon, memory, run, skills, system  # noqa: F401

# names used externally (tui.py, tests): re-export from their new homes
from .commands.auth import _ask_key, _connect_flow, _pick_model_name, _pick_provider
from .commands.system import _code_version
from .resolve import _resolve_model

__all__ = [
    "app",
    "console",
    "_cfg",
    "_make_approver",
    "_make_approver_state",
    "_make_on_tool",
    "_make_on_token",
    "_resolve_model",
    "_code_version",
    "_pick_provider",
    "_ask_key",
    "_connect_flow",
    "_pick_model_name",
]

if __name__ == "__main__":
    app()
