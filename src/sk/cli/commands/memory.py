"""CLI commands: memories + todos sub-apps (split from sk/cli.py, pure move)."""

from __future__ import annotations

import typer

from ..base import app, console


@app.command()
def remember(text: str = typer.Argument(..., help="Fact to save, e.g. 'prefers fast model'")):
    """Save a memory: sk remember \"prefers qwen3:4b\""""
    from sk.store import save_memory

    console.print(f"[green]{save_memory(text)}[/green] {text[:120]}")


@app.command(name="recall")
def recall_cmd(query: str = typer.Argument("", help="Search terms (empty = recent)")):
    """Search memories: sk recall \"model\""""
    from sk.store import recall_memories

    hits = recall_memories(query, limit=10)
    if not hits:
        console.print('[yellow](no memories yet — try `sk remember "..."`)[/yellow]')
        return
    for h in hits:
        console.print(f"• {h}")


@app.command(name="memories")
def memories_cmd():
    """List all memories."""
    from sk.store import list_memories

    hits = list_memories(limit=50)
    if not hits:
        console.print("[yellow](no memories yet)[/yellow]")
        return
    for h in hits:
        console.print(f"• {h}")


@app.command()
def forget(query: str = typer.Argument(..., help="Substring to delete")):
    """Delete matching memories: sk forget \"qwen\""""
    from sk.store import forget_memory

    console.print(f"[yellow]{forget_memory(query)}[/yellow]")


todo_app = typer.Typer(help="Todos: sk todo add/list/done/clear")

app.add_typer(todo_app, name="todo")


@todo_app.command("add")
def todo_add(text: str = typer.Argument(..., help="Todo text")):
    """Add: sk todo add \"clean disk\""""
    from sk.store import add_todo

    console.print(f"[green]{add_todo(text)}[/green]")


@todo_app.command("list")
def todo_list(all: bool = typer.Option(False, "--all", help="Include done")):
    """List: sk todo list"""
    from sk.store import list_todos

    rows = list_todos(open_only=not all)
    if not rows:
        console.print('[dim](no todos — sk todo add "..." )[/dim]')
        return
    for i, t, d in rows:
        mark = "[green]✓[/green]" if d else "[yellow]○[/yellow]"
        console.print(f"{mark} #{i} {t}")


@todo_app.command("done")
def todo_done(tid: int = typer.Argument(..., help="Todo id")):
    """Done: sk todo done 1"""
    from sk.store import complete_todo

    console.print(f"[green]{complete_todo(tid)}[/green]")


@todo_app.command("clear")
def todo_clear():
    """Clear done: sk todo clear"""
    from sk.store import clear_todos

    console.print(f"[yellow]{clear_todos()}[/yellow]")
