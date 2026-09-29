"""CLI package: app + shared pieces + command registration (split from sk/cli.py).

Commands live in .commands/ (imported for side-effect registration).
Bare `sk` (no subcommand) launches the fullscreen TUI — the primary way in.
`sk chat` remains as the plain-text fallback surface.
All names below preserve the old `sk.cli.X` surface.
"""

import typer

from .approvers import _make_approver, _make_approver_state, _make_on_token, _make_on_tool
from .base import _cfg, app, console

# side-effect imports: register all @app.command()s
from .commands import auth, chat, daemon, mcp, memory, run, skills, system  # noqa: F401

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


@app.callback(invoke_without_command=True)
def _default(
    ctx: typer.Context,
    cwd: str = typer.Option("", "--cwd", help="Run as if in this directory"),
    profile: str = typer.Option("", "--profile", help="Use this config profile"),
) -> None:
    """Launch the fullscreen TUI (same as `sk tui`)."""
    if cwd.strip():
        import os
        from pathlib import Path

        target = Path(os.path.expanduser(cwd.strip()))
        if not target.exists():
            console.print(f"[red]Error: --cwd {cwd.strip()}: no such directory.[/red]")
            raise typer.Exit(1)
        if not target.is_dir():
            console.print(f"[red]Error: --cwd {cwd.strip()}: not a directory.[/red]")
            raise typer.Exit(1)
        os.chdir(target)
    if profile.strip():
        import os

        from sk.config import PROFILE_ENV, profile_path

        try:
            path = profile_path(profile.strip())
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        if not path.exists():
            console.print(
                f"[red]no such profile '{profile.strip()}' (sk config --profiles to list).[/red]"
            )
            raise typer.Exit(1)
        os.environ[PROFILE_ENV] = profile.strip()
    if ctx.invoked_subcommand is None:
        from sk.tui import launch

        launch()
