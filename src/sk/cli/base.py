"""CLI shared base: app, console, config loader (split from sk/cli.py, pure move)."""

from __future__ import annotations

import typer
from rich.console import Console

from sk.config import Config

app = typer.Typer(add_completion=True, help="Sidekick - local terminal companion (Ollama)")
console = Console()


def _cfg() -> Config:
    cfg = Config.load()
    cfg.ensure_created()
    return cfg
