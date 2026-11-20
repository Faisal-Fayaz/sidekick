"""CLI command: sk docs check — propose doc updates for changed code (#521)."""

from __future__ import annotations

import json

import typer

from ..base import console

docs_app = typer.Typer(help="Docs freshness checks — propose, never edit")


@docs_app.command("check")
def docs_check(
    since: str = typer.Option("", "--since", help="Git ref to diff against (default: HEAD)"),
    paths: list[str] = typer.Option([], "--paths", help="Check these source paths instead of git"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable report"),
):
    """Report documented names removed by a change. Never writes a file."""
    from sk.docs_guard import check, render

    report = check(since=since, paths=list(paths))
    if as_json:
        typer.echo(json.dumps(report, indent=2))
    else:
        console.print(render(report))
        for rel in report["unavailable"]:
            console.print(f"[dim]no baseline for {rel} (not in git?) — skipped[/dim]")
    if not report["ok"]:
        raise typer.Exit(1)
