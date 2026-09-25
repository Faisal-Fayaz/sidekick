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
    bg: bool = typer.Option(
        False, "--bg", help="Run detached, return a job id, notify on completion"
    ),
    allow: str = typer.Option(
        "", "--allow", help="Auto-approve list, e.g. --allow shell:pytest,write_file"
    ),
):
    """Single-shot: sk run \"summarize disk usage in ~/\""""
    import json as _json

    from sk.config import parse_allow_list
    from sk.jobs import create_job, spawn_worker

    cfg = _cfg()
    cfg.model = _resolve_model(cfg, model, task, quiet=as_json)
    allowed = parse_allow_list(allow)
    if bg:
        job_id = create_job(task, session, cfg.model, yes, cfg.spend_cap_usd, allowed)
        if not job_id or not spawn_worker(job_id):
            msg = "Error: could not start background worker."
            if as_json:
                print(
                    _json.dumps(
                        {
                            "ok": False,
                            "answer": "",
                            "model": cfg.model,
                            "session": session,
                            "tools": [],
                            "error": msg,
                            "job_id": "",
                        }
                    ),
                    flush=True,
                )
                raise typer.Exit(1)
            console.print(f"[red]{msg}[/red]")
            raise typer.Exit(1)
        if as_json:
            print(
                _json.dumps(
                    {
                        "ok": True,
                        "answer": "",
                        "model": cfg.model,
                        "session": session,
                        "tools": [],
                        "error": None,
                        "job_id": job_id,
                    }
                ),
                flush=True,
            )
            return
        console.print(f"[green]started background job {job_id}[/green] (session: {session})")
        console.print("[dim]status: `sk jobs` · transcript lands in the session[/dim]")
        return
    history = get_history(session)
    if not as_json:
        mode = "auto-approve writes" if yes else "confirm writes"
        console.print(f"[dim]task: {task}  model: {cfg.model} ({mode})[/dim]")
    save_message(session, "user", task)

    approve = _make_approver(yes, cfg.approved_commands, allowed)
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


@app.command(name="run-bg-worker", hidden=True)
def run_bg_worker(job_id: str = typer.Argument(..., help="Job id from `sk run --bg`")):
    """Internal: execute one background job (spawned detached by --bg)."""
    from sk.jobs import run_bg_worker as _run

    _run(job_id)


@app.command()
def jobs(
    limit: int = typer.Option(10, "--limit", "-n", help="How many recent jobs to show"),
):
    """List background jobs (`sk run --bg`)."""
    from sk.jobs import load_jobs

    rows = sorted(load_jobs().items(), key=lambda kv: kv[1].get("created", 0), reverse=True)
    if not rows:
        console.print('[dim](no background jobs yet — start one with `sk run --bg "task"`)[/dim]')
        return
    for jid, j in rows[: max(1, limit)]:
        status = str(j.get("status", "?"))
        mark = (
            "[green]done[/green]"
            if status == "done"
            else ("[red]error[/red]" if status == "error" else "[yellow]running[/yellow]")
        )
        console.print(f"• [cyan]{jid}[/cyan] {mark} [dim]({j.get('session', '?')})[/dim]")
        console.print(f"  [dim]{str(j.get('task', ''))[:100]}[/dim]")
        if status == "error" and j.get("error"):
            console.print(f"  [red]{str(j.get('error'))[:200]}[/red]")
