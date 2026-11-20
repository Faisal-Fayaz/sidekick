"""CLI command: sk verify-browser — an optional local browser check (#520)."""

from __future__ import annotations

import json

import typer

from ..base import app, console


@app.command("verify-browser")
def verify_browser(
    url: str = typer.Argument(..., help="The http/https URL to verify"),
    snapshot: str = typer.Option(
        "", "--snapshot", help="Write the page text/accessibility tree here"
    ),
    screenshot: str = typer.Option("", "--screenshot", help="Write a screenshot here"),
    emit_test: str = typer.Option("", "--emit-test", help="Write a Playwright test skeleton here"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable result"),
):
    """Open one URL behind the egress/SSRF gates and capture what it renders."""
    from sk.browser_verify import verify

    result = verify(url, snapshot=snapshot, screenshot=screenshot, emit_test=emit_test)
    if as_json:
        typer.echo(json.dumps(result, indent=2))
    else:
        if not result["available"]:
            console.print(f"[yellow]{result['note']}[/yellow]")
        elif not result["ok"]:
            console.print(f"[red]{result['note'] or 'browser verification failed'}[/red]")
        else:
            console.print(f"[green]verified {result['target']} with {result['browser']}[/green]")
            if result["note"]:
                console.print(f"[dim]{result['note']}[/dim]")
        for key in ("snapshot", "screenshot", "test"):
            if result[key]:
                console.print(f"  {key}: {result[key]}")
    if result["available"] and not result["ok"]:
        raise typer.Exit(1)
