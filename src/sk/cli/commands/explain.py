"""CLI command: sk explain — a grounded explanation of code or a diff.

The last rung of the developer ladder. An assistant that explains a codebase
from memory is the failure mode this whole project is built against, so the
answer has to come from the code: the agent reads the repository with the
approval-free tools, and says plainly when the code does not answer the
question.

Read-only by construction: the run is in read-only mode, so no approval-gated
tool — no write, no shell — is reachable from it.
"""

from __future__ import annotations

import json
from contextlib import nullcontext

import typer

from ..approvers import _make_approver
from ..base import _cfg, app, console, data_mode

EXPLAIN_PROMPT = """{question}

Answer from the code in this repository, not from memory:

- read the relevant files before you answer — search for them, then open them;
- cite `file:line` for every claim you make about what the code does;
- if the code does not answer the question, say so plainly. Do not guess, and do
  not fill the gap with what such a project usually does — "I could not find
  that in this repository" is a correct answer.

You are reading, not changing: nothing here may be written.
"""


@app.command("explain")
def explain_cmd(
    question: str = typer.Argument(..., help="What you want explained"),
    as_json: bool = typer.Option(False, "--json", help="Emit a machine-readable report"),
    session: str = typer.Option("explain", help="Audit session name"),
):
    """Explain the code, grounded in it, with file:line citations.

    The agent works read-only: it can search the repository and read files, and
    nothing else. It is asked to cite `file:line` for its claims and to say
    plainly when the code does not answer the question.
    """
    from sk.agent import run_agent
    from sk.check.runner import EXIT_CONFIG

    cfg = _cfg()
    # The read tools are approval-free, so nothing here can prompt or write.
    approve = _make_approver(True, cfg.approved_commands, (), deny=())

    with data_mode() if as_json else nullcontext():
        answer = run_agent(
            EXPLAIN_PROMPT.format(question=question),
            [],
            cfg,
            approve=approve,
            session=session,
            read_only=True,
        )

    if as_json:
        typer.echo(json.dumps({"question": question, "answer": answer}, indent=2))
    else:
        console.print(answer)
    raise typer.Exit(EXIT_CONFIG if not answer else 0)
