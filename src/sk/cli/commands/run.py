"""CLI commands: single-shot agent run (split from sk/cli.py, pure move)."""

from __future__ import annotations

import typer
from rich.markdown import Markdown

from sk.agent import run_agent
from sk.store import get_history, save_message

from ..approvers import (
    _make_approver,
    _make_on_reasoning,
    _make_on_token,
    _make_on_tool,
    _make_plan_reviewer,
)
from ..base import _cfg, app, console
from ..resolve import _resolve_model


@app.command()
def run(
    task: str = typer.Argument(..., help='Task in quotes, e.g. "summarize disk usage"'),
    session: str = typer.Option("default", help="Set the session name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve writes (else prompts)"),
    read_only: bool = typer.Option(
        False, "--read-only", help="Block all file writes (research mode, beats --yes)"
    ),
    plan: bool = typer.Option(
        False, "--plan", help="Propose a plan without writing files (beats --yes)"
    ),
    model: str = typer.Option("auto", help="Pick a model: auto (router), fast, smart, or name"),
    no_stream: bool = typer.Option(False, "--no-stream", help="Disable live token streaming"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON + exit codes"),
    bg: bool = typer.Option(
        False, "--bg", help="Run detached, return a job id, notify on completion"
    ),
    allow: str = typer.Option(
        "", "--allow", help="Auto-approve list, e.g. --allow shell:pytest,write_file"
    ),
    deny: str = typer.Option(
        "", "--deny", help="Never-approve list, e.g. --deny shell,write_file (beats --allow)"
    ),
):
    """Run a one-shot task: sk run "summarize disk usage in ~/" """
    import json as _json

    from sk.config import parse_allow_list
    from sk.jobs import create_job, spawn_worker

    cfg = _cfg()
    cfg.model = _resolve_model(cfg, model, task, quiet=as_json)
    allowed = parse_allow_list(allow)
    denied = parse_allow_list(deny)
    if bg:
        import os

        from sk.config import PROFILE_ENV

        job_id = create_job(
            task,
            session,
            cfg.model,
            yes,
            cfg.spend_cap_usd,
            allowed,
            read_only,
            plan,
            os.getenv(PROFILE_ENV, ""),
            denied,
        )
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
        if read_only:
            mode = "read-only"
        elif plan:
            mode = "plan"
        else:
            mode = "auto-approve writes" if yes else "confirm writes"
        console.print(f"[dim]task: {task}  model: {cfg.model} ({mode})[/dim]")
    save_message(session, "user", task)

    approve = _make_approver(
        yes, cfg.approved_commands, allowed, readonly=read_only, plan_mode=plan, deny=denied
    )
    used_tools: list[str] = []

    def _recorder(name: str, args: dict) -> None:
        if name not in used_tools:
            used_tools.append(name)

    stream = {"n": 0, "cleared": False, "clear_line": True}
    on_tool = _recorder if as_json else _make_on_tool(stream)
    on_token = None if (no_stream or as_json) else _make_on_token(stream)
    on_reasoning = None if (no_stream or as_json) else _make_on_reasoning()

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
        console.print("[dim]working... (streams live)[/dim]", end="")
    try:
        answer = run_agent(
            task,
            history,
            cfg,
            on_tool=on_tool,
            on_token=on_token,
            approve=approve,
            on_reasoning=on_reasoning,
            auto_approve=yes,
            session=session,
            review_plan=_make_plan_reviewer({"yolo": yes, "readonly": read_only, "plan": plan}),
            read_only=read_only,
            plan_mode=plan,
        )
        # Fast→smart escalation (refs #275): when the router owned the pick
        # (auto) and chose fast, a failed turn retries once on smart. Single
        # retry by construction — no loop, so no ping-pong. Interactive chat
        # sessions are excluded (mid-session model swaps would confuse).
        if (model or "auto").strip().lower() == "auto":
            from sk.router import pick_tier, should_escalate

            if pick_tier(task)[0] == "fast" and should_escalate(answer):
                from sk.config import resolve_alias

                cfg.model = resolve_alias(cfg.provider, "smart", cfg.model)
                if not as_json:
                    console.print(
                        "\n[dim]escalated fast→smart (first turn failed) — retrying once[/dim]"
                    )
                answer = run_agent(
                    task,
                    history,
                    cfg,
                    on_tool=on_tool,
                    on_token=on_token,
                    approve=approve,
                    on_reasoning=on_reasoning,
                    auto_approve=yes,
                    session=session,
                    review_plan=_make_plan_reviewer(
                        {"yolo": yes, "readonly": read_only, "plan": plan}
                    ),
                    read_only=read_only,
                    plan_mode=plan,
                )
    except Exception as e:
        if as_json:
            _emit(False, "", str(e)[:500])
            raise typer.Exit(1)
        console.print("\r\x1b[2K", end="")
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    save_message(session, "assistant", answer)
    if as_json:
        _emit(True, answer or "", None)
        return
    streamed = getattr(on_token, "state", {}).get("n", 0) if on_token else 0
    if on_token is None or streamed < len(answer or "") * 0.5 or not (answer or "").strip():
        console.print("\r\x1b[2K", end="")
        console.print(Markdown(answer or "(empty)"))
    else:
        console.print()
    console.print("[dim]--- done ---[/dim]")


@app.command(name="run-bg-worker", hidden=True)
def run_bg_worker(job_id: str = typer.Argument(..., help="Run this job id from `sk run --bg`")):
    """Internal: execute one background job (spawned detached by --bg)."""
    from sk.jobs import run_bg_worker as _run

    _run(job_id)


@app.command()
def jobs(
    limit: int = typer.Option(10, "--limit", "-n", help="Show this many recent jobs"),
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
