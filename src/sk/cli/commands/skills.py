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
    name: str = typer.Argument("superpowers", help="Preset, registry name, or git URL"),
    force: bool = typer.Option(False, "--force", help="Re-clone if present"),
):
    """Install skill packs: sk skills-install superpowers"""
    from sk.skills import install_pack

    console.print(f"[dim]installing {name}...[/dim]")
    out = install_pack(name, force=force)
    if out.startswith("Installed"):
        console.print(f"[green]{out}[/green]")
    else:
        console.print(f"[yellow]{out}[/yellow]")


@app.command()
def plugins():
    """List user-defined tools from ~/.sidekick/skills/*/TOOLS.md (+ warnings)."""
    from sk.plugins import TOOLS_FILENAME, _skills_dir, load_specs

    specs, warnings = load_specs()
    console.print(f"[dim]{_skills_dir()}/{TOOLS_FILENAME} — {len(specs)} tools[/dim]")
    for spec in specs:
        gate = "ask" if spec.get("approval") == "ask" else "auto"
        console.print(
            f"• [cyan]{spec['name']}[/cyan] [{spec.get('kind')}/{gate}] — {spec.get('description', '')[:100]}"
        )
    for w in warnings:
        console.print(f"[yellow]! {w[:160]}[/yellow]")
    if not specs:
        console.print("[dim]Add one: see docs/plugins.md[/dim]")


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


@app.command(name="skills-registry")
def skills_registry(
    query: str = typer.Argument("", help="Keywords (empty = list the whole registry)"),
):
    """Browse installable packs beyond superpowers: sk skills-registry agents"""
    from sk.skills import REGISTRY, search_registry

    rows = search_registry(query)
    label = f" for '{query}'" if query.strip() else ""
    console.print(
        f"[dim]registry — {len(rows)} pack{'' if len(rows) == 1 else 's'}{label} (vendored, offline)[/dim]"
    )
    if not rows:
        console.print("[yellow]no matches — try different words[/yellow]")
        return
    for entry in rows:
        console.print(f"• [cyan]{entry['name']}[/cyan] — {entry['description'][:120]}")
    console.print(f"[dim]install: sk skills-install <name> ({len(REGISTRY)} in registry)[/dim]")
