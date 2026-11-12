"""CLI command: sk test — run the project's tests through the check pipeline.

Same machinery as ``sk check``: the same approval gate, the same hooks, the
same audit ledger, the same parsers, the same exit codes. The difference is
scope — the project's test command, whatever it is declared to be.

The command comes from the project (``.sidekick/checks.toml``, kind ``test``)
or from detection (``docs``: a Makefile target, a ``package.json`` script,
``cargo test``, ``go test ./...``, ``pytest -q``). It is never invented.

A missing test runner is ``unknown``, never a pass.
"""

from __future__ import annotations

import json
from contextlib import nullcontext as _nullcontext

import typer

from ..approvers import _make_approver
from ..base import _cfg, app, console, data_mode


@app.command("test")
def test_cmd(
    command: str = typer.Option(
        "", "--command", help="Run this instead of the project's test command"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit a machine-readable report"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve the test command"),
    allow: str = typer.Option("", "--allow", help="Auto-approve list, e.g. --allow shell:pytest"),
    deny: str = typer.Option("", "--deny", help="Never-approve list, e.g. --deny shell"),
    trust_project_checks: bool = typer.Option(
        False,
        "--trust-project-checks",
        help="Load repo-supplied .sidekick/checks.toml (untrusted by default)",
    ),
    max_diagnostics: int = typer.Option(
        50, "--max-diagnostics", help="Cap diagnostics returned per check"
    ),
    session: str = typer.Option("test", help="Audit session name"),
):
    """Run the project's tests with structured diagnostics.

    Uses the check the project declares (``.sidekick/checks.toml``, kind
    ``test``) or detects one: a Makefile target, a ``package.json`` script,
    ``cargo test``, ``go test ./...``, ``pytest -q``. The command still goes
    through the normal shell approval gate and audit log.

    Exit status is 0 on pass, 1 on failure or unknown, and 2 on a config or
    trust error. A missing test runner is reported as unknown, not as a pass.
    """
    from sk.check import load_checks_pack, run_checks
    from sk.check.config import CheckCommand, CheckPack
    from sk.check.report import format_compact
    from sk.check.runner import EXIT_CONFIG, MAX_DIAGNOSTICS
    from sk.check.testcmd import detect_test_command

    if max_diagnostics < 1:
        console.print("[red]--max-diagnostics must be at least 1[/red]")
        raise typer.Exit(EXIT_CONFIG)

    pack = load_checks_pack(trust_project=trust_project_checks)

    if pack.errors:
        for error in pack.errors:
            console.print(f"[red]error: {error}[/red]")
        raise typer.Exit(EXIT_CONFIG)
    if pack.present and not pack.trusted:
        console.print(
            "[yellow]Project checks are untrusted by default.[/yellow] "
            "Pass --trust-project-checks (or set SIDEKICK_TRUST_REPO=1) to load them."
        )
        raise typer.Exit(EXIT_CONFIG)

    test_checks = [c for c in pack.checks if c.kind == "test"]
    if command.strip():
        # An explicit command replaces the project's declaration, and runs
        # through the same pipeline.
        test_checks = [CheckCommand(id="test", command=command.strip(), kind="test", parser="auto")]
        pack = CheckPack(
            root=pack.root,
            path=pack.path,
            present=False,
            trusted=True,
            checks=tuple(test_checks),
            declared=(),
        )
    elif not test_checks:
        detected = detect_test_command(pack.root)
        if not detected:
            console.print(
                "[red]No test command found.[/red] Declare one in .sidekick/checks.toml "
                '(kind = "test"), or pass --command.'
            )
            raise typer.Exit(EXIT_CONFIG)
        console.print(f"[dim]detected test command: {detected}[/dim]")
        pack = CheckPack(
            root=pack.root,
            path=pack.path,
            present=False,
            trusted=True,
            checks=(CheckCommand(id="test", command=detected, kind="test", parser="auto"),),
            declared=(),
        )

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
    with data_mode() if as_json else _nullcontext():
        report = run_checks(
            pack=pack,
            kinds=("test",),
            trust_project=trust_project_checks,
            approve=approve,
            session=session,
            provider=cfg.provider,
            max_diagnostics=max(1, min(MAX_DIAGNOSTICS * 10, max_diagnostics)),
        )

    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2))
    else:
        console.print(format_compact(report), markup=False)

    raise typer.Exit(report.exit_code)
