"""CLI command: sk team check — show what the team layer would load, and why.

```bash
sk team check
sk team check --json
```

The command reads files only. It never runs a command declared by a shared
skill and never widens a permission; it just reports what the loaders decided.
"""

from __future__ import annotations

import json

import typer

from ..base import console

team_app = typer.Typer(help="Inspect team memory and shared skill packs")


@team_app.command("check")
def check(
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Report the team layer's trust state, memories, skills and refusals."""
    import os

    from sk.team import status

    report = status(project_root=os.getcwd())

    if as_json:
        # Echo raw: Rich's console wraps long lines, which breaks JSON.
        typer.echo(json.dumps(report, indent=2))
        return

    trust = (
        "[green]trusted[/green]" if report["project_trusted"] else "[yellow]not trusted[/yellow]"
    )
    console.print(f"project layer: {trust}")
    console.print(
        f"policy: {report['policy']} (allow_tools: {', '.join(report['allow_tools']) or '(none)'})"
    )
    console.print("[bold]team memories[/bold]")
    for mem in report["memories"]:
        note = " [dim]fenced[/dim]" if mem["source"] == "project" else ""
        console.print(f"  - \\[team/{mem['source']}] {mem['content']}{note}")
    if not report["memories"]:
        console.print("  (none)")
    console.print("[bold]shared skills[/bold]")
    for skill in report["skills"]:
        tools = ", ".join(skill["tools"]) or "no tools requested"
        console.print(f"  - \\[team/{skill['source']}] {skill['name']} ({tools})")
    if not report["skills"]:
        console.print("  (none)")
    if report["errors"]:
        console.print("[bold red]refusals[/bold red]")
        for error in report["errors"]:
            console.print(f"  - {error}")

    if not report["project_trusted"]:
        console.print(
            "[dim]Project files are ignored. Set SIDEKICK_TRUST_REPO=1 in your "
            "shell to load them (never in a repo file).[/dim]"
        )
