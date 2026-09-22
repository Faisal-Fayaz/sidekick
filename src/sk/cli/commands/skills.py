"""CLI commands: skill listing + installer (split from sk/cli.py, pure move)."""

from __future__ import annotations

import typer

from ..base import app, console


@app.command()
def skills():
    """List skill packs in ~/.sidekick/skills/ (auto-loaded into prompt)."""
    from sk.skills import SKILLS_DIR, list_skills

    rows = list_skills()
    console.print(f"[dim]{SKILLS_DIR} — {len(rows)} packs[/dim]")
    for name, size in rows:
        console.print(f"• [cyan]{name}[/cyan] ({size}b)")
    console.print("[dim]Add your own: echo '# my skill\\n- rule' > ~/.sidekick/skills/my.md[/dim]")


@app.command(name="skills-install")
def skills_install(
    name: str = typer.Argument("superpowers", help="Preset (superpowers) or git URL"),
    force: bool = typer.Option(False, "--force", help="Re-clone if present"),
):
    """Install skill packs: sk skills-install superpowers"""
    from sk.skills import install_preset

    console.print(f"[dim]installing {name}...[/dim]")
    out = install_preset(name, force=force)
    if out.startswith("Installed"):
        console.print(f"[green]{out}[/green]")
    else:
        console.print(f"[yellow]{out}[/yellow]")


@app.command(name="skills-search")
def skills_search(
    query: str = typer.Argument("", help="Keywords (empty = list all packs)"),
):
    """Search skill packs by keyword: sk skills-search debug"""
    from sk.skills import SKILLS_DIR, search_skills

    rows = search_skills(query)
    label = f" for '{query}'" if query.strip() else ""
    console.print(
        f"[dim]{SKILLS_DIR} — {len(rows)} match{'' if len(rows) == 1 else 'es'}{label}[/dim]"
    )
    if not rows:
        console.print(
            "[yellow]no matches — try different words or `sk skills-install superpowers`[/yellow]"
        )
        return
    for name, desc in rows:
        console.print(f"• [cyan]{name}[/cyan]" + (f" — {desc[:120]}" if desc else ""))
