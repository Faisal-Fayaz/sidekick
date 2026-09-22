"""CLI commands: single-shot agent run (split from sk/cli.py, pure move)."""

from __future__ import annotations

import typer
from rich.markdown import Markdown

from sk.agent import run_agent
from sk.store import get_history, save_message

from ..approvers import _make_approver, _make_on_token, _make_on_tool
from ..base import _cfg, app, console
from ..resolve import _resolve_model


@app.command()
def run(
    task: str = typer.Argument(..., help='Task in quotes, e.g. "summarize disk usage"'),
    session: str = typer.Option("default", help="Session name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve writes (else prompts)"),
    model: str = typer.Option("auto", help="Model: auto (router), fast, smart, or name"),
    no_stream: bool = typer.Option(False, "--no-stream", help="Disable live token streaming"),
):
    """Single-shot: sk run \"summarize disk usage in ~/\""""
    cfg = _cfg()
    cfg.model = _resolve_model(cfg, model, task)
    history = get_history(session)
    mode = "auto-approve writes" if yes else "confirm writes"
    console.print(f"[dim]task: {task}  model: {cfg.model} ({mode})[/dim]")
    save_message(session, "user", task)

    approve = _make_approver(yes, cfg.approved_commands)
    on_tool = _make_on_tool()
    on_token = None if no_stream else _make_on_token()

    console.print("[dim]working... (streams live)[/dim]")
    try:
        answer = run_agent(
            task,
            history,
            cfg,
            on_tool=on_tool,
            on_token=on_token,
            approve=approve,
            auto_approve=yes,
            session=session,
        )
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    save_message(session, "assistant", answer)
    console.print()
    streamed = getattr(on_token, "state", {}).get("n", 0) if on_token else 0
    if on_token is None or streamed < len(answer or "") * 0.5:
        console.print(Markdown(answer or "(empty)"))
    console.print("[dim]--- done ---[/dim]")
