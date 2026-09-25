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

    from sk.daemon import append_log, check_once, load_state, notify, save_state

    def run_one() -> int:
        nudges, state = check_once(load_state(), disk_warn=disk_warn)
        save_state(state)
        if nudges:
            for n in nudges:
                console.print(f"[yellow]! {n}[/yellow]")
            append_log(nudges)
            for n in nudges[:3]:
                notify("sidekick", n, force=once)
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


@app.command(name="daemon-install")
def daemon_install(
    interval: int = typer.Option(300, "--interval", help="Seconds between checks"),
    disk_warn: int = typer.Option(90, "--disk-warn", help="Disk % threshold"),
):
    """Install the watcher as a user systemd service (Linux)."""
    from sk.daemon import UNIT_NAME, install_unit

    out = install_unit(interval=interval, disk_warn=disk_warn)
    if out.startswith("Installed + started"):
        console.print(f"[green]{out}[/green]")
    elif out.startswith("Installed"):
        console.print(f"[yellow]{out}[/yellow]")
    else:
        console.print(f"[red]{out}[/red]")
    console.print(f"[dim]Logs: ~/.sidekick/nudges.log · unit: {UNIT_NAME}[/dim]")


@app.command(name="daemon-install-macos")
def daemon_install_macos(
    interval: int = typer.Option(300, "--interval", help="Seconds between checks"),
    disk_warn: int = typer.Option(90, "--disk-warn", help="Disk % threshold"),
):
    """Install the watcher as a launchd agent (macOS)."""
    from sk.daemon import LAUNCHD_LABEL, install_launchd

    out = install_launchd(interval=interval, disk_warn=disk_warn)
    if out.startswith("Installed + started"):
        console.print(f"[green]{out}[/green]")
    elif out.startswith("Installed"):
        console.print(f"[yellow]{out}[/yellow]")
    else:
        console.print(f"[red]{out}[/red]")
    console.print(f"[dim]Logs: ~/.sidekick/nudges.log · label: {LAUNCHD_LABEL}[/dim]")
