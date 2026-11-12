"""CLI shared base: app, console, config loader (split from sk/cli.py, pure move)."""

from __future__ import annotations

from contextlib import contextmanager

import typer
from rich.console import Console

from sk.config import Config

app = typer.Typer(add_completion=True, help="Sidekick - local terminal companion (Ollama)")
console = Console()
# The approval notices are human-facing; stdout is the data channel when a
# command emits JSON. `data_mode` moves the notices to stderr so a report can
# be piped into a parser without the approval card breaking it.
err_console = Console(stderr=True)


@contextmanager
def data_mode():
    """Route approval notices to stderr while stdout carries the data."""
    from . import approvers

    previous = approvers.console
    approvers.console = err_console
    try:
        yield
    finally:
        approvers.console = previous


def _cfg() -> Config:
    cfg = Config.load()
    cfg.ensure_created()
    return cfg
