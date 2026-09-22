"""CLI commands: background watcher (split from sk/cli.py, pure move)."""

from __future__ import annotations

import time

import typer

from ..base import app, console


@app.command()
def daemon(
    once: bool = typer.Option(False, "--once", help="Single check, then exit"),
    interval: int = typer.Option(300, "--interval", help="Seconds between checks in loop mode"),
    disk_warn: int = typer.Option(90, "--disk-warn", help="Disk % threshold"),
):
    """Watcher: disk + shell failures + dirty repos. Loop foreground; use --once for cron."""

    from sk.daemon import append_log, check_once, load_state, save_state

    def run_one() -> int:
        nudges, state = check_once(load_state(), disk_warn=disk_warn)
        save_state(state)
        if nudges:
            for n in nudges:
                console.print(f"[yellow]! {n}[/yellow]")
            append_log(nudges)
        else:
            console.print("[green]clean — no nudges.[/green]")
        return len(nudges)

    if once:
        run_one()
        return
    console.print(
        f"[dim]daemon loop every {interval}s (Ctrl-C to stop). Log: ~/.sidekick/nudges.log[/dim]"
    )
    try:
        while True:
            run_one()
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\nstopped.")
