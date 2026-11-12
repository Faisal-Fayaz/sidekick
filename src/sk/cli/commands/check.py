"""CLI command: sk check — run the project's configured checks.

Every command goes through the same shell approval gate and audit ledger as an
agent tool call. Project checks are untrusted by default; the file is only
loaded with ``--trust-project-checks`` or ``SIDEKICK_TRUST_REPO=1``.
"""

from __future__ import annotations

import json
from contextlib import nullcontext

import typer

from ..approvers import _make_approver
from ..base import _cfg, app, console, data_mode


@app.command("check")
def check_cmd(
    changed: bool = typer.Option(
        False, "--changed", help="Only run checks relevant to changed files"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit a machine-readable report"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve check commands"),
    allow: str = typer.Option("", "--allow", help="Auto-approve list, e.g. --allow shell:pytest"),
    deny: str = typer.Option("", "--deny", help="Never-approve list, e.g. --deny shell"),
    trust_project_checks: bool = typer.Option(
        False,
        "--trust-project-checks",
        help="Load repo-supplied .sidekick/checks.toml (untrusted by default)",
    ),
    check_config: bool = typer.Option(
        False, "--check-config", help="Validate .sidekick/checks.toml and exit"
    ),
    explain: bool = typer.Option(False, "--explain", help="Show the loaded checks and exit"),
    max_diagnostics: int = typer.Option(
        50, "--max-diagnostics", help="Cap diagnostics returned per check"
    ),
    session: str = typer.Option("check", help="Audit session name"),
):
    """Run the project's configured checks with structured diagnostics.

    The checks file is `.sidekick/checks.toml`. It is untrusted by default;
    pass `--trust-project-checks` (or set `SIDEKICK_TRUST_REPO=1`) to load it.
    Each command still goes through the normal shell approval gate and audit
    log. Exit status is 0 on pass, 1 on failure or unknown, and 2 on a config
    or trust error.
    """
    from sk.check import load_checks_pack, run_checks
    from sk.check.report import format_compact
    from sk.check.runner import EXIT_CONFIG, MAX_DIAGNOSTICS

    if max_diagnostics < 1:
        console.print("[red]--max-diagnostics must be at least 1[/red]")
        raise typer.Exit(EXIT_CONFIG)

    pack = load_checks_pack(trust_project=trust_project_checks)

    if explain or check_config:
        if explain:
            typer.echo(pack.explain())
        if check_config:
            issues = pack.check()
            for level, message in issues:
                color = "red" if level == "error" else "yellow"
                console.print(f"[{color}]{level}[/{color}]: {message}")
            if not issues:
                console.print("[green]Checks config OK[/green]")
            elif not any(level == "error" for level, _ in issues):
                console.print("[green]Checks config OK (warnings only)[/green]")
            if any(level == "error" for level, _ in issues):
                raise typer.Exit(EXIT_CONFIG)
        return

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
    with data_mode() if as_json else nullcontext():
        report = run_checks(
            pack=pack,
            changed=changed,
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
