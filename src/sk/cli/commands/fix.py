"""CLI command: sk fix — investigate, change, and prove it.

The guided-change rung of the developer ladder: one command that takes a
problem, makes a bounded change, and reports whether the change actually
works.

The agent investigates and edits under the normal approval gate, and its edits
are checkpointed, so `/rewind` can undo them. The post-edit verification gate
then judges what changed — syntax plus the project's configured lint/typecheck
checks — and a failure buys a targeted repair round, exactly as it does for any
other edit.

What this command adds is the *verdict*: the pass / fail / unknown of the
verification is reported as a verdict rather than left in prose, and a failing
check does not ship as "done".
"""

from __future__ import annotations

import json
from contextlib import nullcontext

import typer

from ..approvers import _make_approver
from ..base import _cfg, app, console, data_mode

FIX_PROMPT = """The user asked for this fix:

{problem}

Investigate first — read the code and find the cause — then make the change.
Keep it scoped to the problem: do not refactor beyond it, and do not change
code that is not part of it. If the cause is not in the code, say so plainly
instead of changing something unrelated.

Your change will be checked afterwards (syntax, plus the project's configured
lint and typecheck checks). Make it compile and make it pass them.
"""


@app.command("fix")
def fix_cmd(
    problem: str = typer.Argument(..., help="What is wrong, in a sentence"),
    as_json: bool = typer.Option(False, "--json", help="Emit a machine-readable report"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve the agent's writes"),
    allow: str = typer.Option("", "--allow", help="Auto-approve list, e.g. --allow write_file"),
    deny: str = typer.Option("", "--deny", help="Never-approve list, e.g. --deny shell"),
    trust_project_checks: bool = typer.Option(
        False,
        "--trust-project-checks",
        help="Load repo-supplied .sidekick/checks.toml for the verification (untrusted by default)",
    ),
    verify_full: bool = typer.Option(
        False, "--verify-full", help="Run every configured check, not just lint/typecheck"
    ),
    session: str = typer.Option("fix", help="Audit session name"),
):
    """Investigate and fix a problem, then prove the fix works.

    The agent works under the normal approval gate and its edits are
    checkpointed, so `/rewind` can undo them. Afterwards the changed files are
    verified — syntax plus the project's configured lint/typecheck checks — and
    a failure buys a targeted repair round.

    Exit status is 0 when the verification passed, 1 when it failed or could
    not be determined (`unknown` is never a pass), and 2 on a config error.
    """
    from sk.agent import run_agent

    cfg = _cfg()
    from sk.config import parse_allow_list

    approved = parse_allow_list(allow)
    denied = parse_allow_list(deny)
    approve = _make_approver(
        yes,
        cfg.approved_commands,
        approved,
        deny=denied,
    )

    edited: list[str] = []

    def on_tool(name: str, args: dict, _result) -> None:
        if name in ("write_file", "edit_file"):
            path = str((args or {}).get("path", "")).strip()
            if path and path not in edited:
                edited.append(path)

    sink: dict = {}
    with data_mode() if as_json else nullcontext():
        answer = run_agent(
            FIX_PROMPT.format(problem=problem),
            [],
            cfg,
            on_tool=on_tool,
            approve=approve,
            session=session,
            trust_project_checks=trust_project_checks,
            verify_full=verify_full,
            verify_sink=sink,
        )

    verdict = str(sink.get("status") or "skipped")
    report = sink.get("report")
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "problem": problem,
                    "answer": answer,
                    "edited": list(edited),
                    "verdict": verdict,
                    "repair_rounds": int(sink.get("repairs_used") or 0),
                    "verification": report.to_dict() if report is not None else None,
                },
                indent=2,
            )
        )
    else:
        console.print(answer)
        if report is None:
            console.print("[dim]verification: skipped — nothing was changed[/dim]")
        else:
            console.print(report.compact(), markup=False)

    raise typer.Exit(0 if verdict in ("pass", "skipped") else 1)
