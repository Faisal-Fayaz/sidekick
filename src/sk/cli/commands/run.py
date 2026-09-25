"""CLI commands: single-shot agent run (split from sk/cli.py, pure move)."""

from __future__ import annotations

import typer
from rich.markdown import Markdown

from sk.agent import run_agent
from sk.store import get_history, save_message

from ..approvers import _make_approver, _make_on_token, _make_on_tool, _make_plan_reviewer
from ..base import _cfg, app, console
from ..resolve import _resolve_model


@app.command()
def run(
    task: str = typer.Argument(..., help='Task in quotes, e.g. "summarize disk usage"'),
    session: str = typer.Option("default", help="Session name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve writes (else prompts)"),
    model: str = typer.Option("auto", help="Model: auto (router), fast, smart, or name"),
    no_stream: bool = typer.Option(False, "--no-stream", help="Disable live token streaming"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output + exit codes"),
):
    """Single-shot: sk run \"summarize disk usage in ~/\""""
    import json as _json

    cfg = _cfg()
    cfg.model = _resolve_model(cfg, model, task, quiet=as_json)
    history = get_history(session)
    if not as_json:
        mode = "auto-approve writes" if yes else "confirm writes"
        console.print(f"[dim]task: {task}  model: {cfg.model} ({mode})[/dim]")
    save_message(session, "user", task)

    approve = _make_approver(yes, cfg.approved_commands)
    used_tools: list[str] = []

    def _recorder(name: str, args: dict) -> None:
        if name not in used_tools:
            used_tools.append(name)

    on_tool = _recorder if as_json else _make_on_tool()
    on_token = None if (no_stream or as_json) else _make_on_token()

    def _emit(ok: bool, answer: str, error: str | None) -> None:
        # plain print: rich would wrap long lines and parse [] as markup,
        # either of which corrupts machine-readable output.
        print(
            _json.dumps(
                {
                    "ok": ok,
                    "answer": answer,
                    "model": cfg.model,
                    "session": session,
                    "tools": used_tools,
                    "error": error,
                }
            ),
            flush=True,
        )

    if not as_json:
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
            review_plan=_make_plan_reviewer({"yolo": yes}),
        )
    except Exception as e:
        if as_json:
            _emit(False, "", str(e)[:500])
            raise typer.Exit(1)
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    save_message(session, "assistant", answer)
    if as_json:
        _emit(True, answer or "", None)
        return
    console.print()
    streamed = getattr(on_token, "state", {}).get("n", 0) if on_token else 0
    if on_token is None or streamed < len(answer or "") * 0.5:
        console.print(Markdown(answer or "(empty)"))
    console.print("[dim]--- done ---[/dim]")
