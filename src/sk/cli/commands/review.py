"""CLI command: sk review — evidence-backed review of a diff or PR."""

from __future__ import annotations

import json

import typer
from rich.markdown import Markdown

from sk.review.engine import ReviewError, ReviewOptions, review
from sk.review.pack import load_review_pack
from sk.review.schema import fail_on_threshold

from ..base import _cfg, app, console

_FAIL_VALUES = ("blocker", "major", "minor", "nit", "never")


@app.command("review")
def review_cmd(
    base: str = typer.Option("", "--base", help="Review the branch against this base ref"),
    staged: bool = typer.Option(False, "--staged", help="Review staged changes only"),
    commit: str = typer.Option("", "--commit", help="Review one commit"),
    pr: int = typer.Option(0, "--pr", help="Review a pull request (GitHub or Bitbucket)"),
    path: str = typer.Option("", "--path", help="Limit the diff to a path"),
    intent: str = typer.Option(
        "", "--intent", help="State the change intent for the worth-it assessment"
    ),
    assess: bool = typer.Option(False, "--assess", help="Force the advisory worth-it assessment"),
    no_assess: bool = typer.Option(False, "--no-assess", help="Disable the worth-it assessment"),
    post: bool = typer.Option(
        False, "--post", help="Post the review to the PR (errors if unavailable)"
    ),
    no_post: bool = typer.Option(False, "--no-post", help="Never post to the PR"),
    as_json: bool = typer.Option(False, "--json", help="Emit the full report as JSON"),
    sarif: bool = typer.Option(False, "--sarif", help="Emit SARIF 2.1.0 for CI"),
    fail_on: str = typer.Option(
        "", "--fail-on", help="Exit 1 at or above: blocker|major|minor|nit|never (default: pack)"
    ),
    model: str = typer.Option("", "--model", help="Override the review model"),
    max_findings: int | None = typer.Option(None, "--max-findings", help="Cap the finding list"),
    context_lines: int | None = typer.Option(
        None, "--context-lines", help="Evidence context window"
    ),
    explain: bool = typer.Option(False, "--explain", help="Show the merged review pack and exit"),
    check_config: bool = typer.Option(
        False, "--check-config", help="Validate review-pack files and exit"
    ),
    trust_project_rules: bool = typer.Option(
        False,
        "--trust-project-rules",
        help="Load repo-supplied review rules (untrusted by default)",
    ),
    disable: list[str] = typer.Option([], "--disable", help="Disable a rule ID (repeatable)"),
    severity: list[str] = typer.Option(
        [], "--severity", help="Override a rule severity as RULE=LEVEL (repeatable)"
    ),
):
    """Review a diff with evidence-backed findings and an advisory worth-it check.

    Defaults to the working tree. Use `--base main` before a PR, `--staged`
    before committing, or `--pr N` to review and (when credentials exist)
    comment on the pull request. Review never edits source files.

    Policy comes from `~/.sidekick/review.toml` and, with
    `--trust-project-rules`, `<repo>/.sidekick/review.toml`. Use `--explain`
    to see the merged policy and `--check-config` to validate it.
    """
    if sum(bool(x) for x in (base.strip(), commit.strip(), pr, staged)) > 1:
        console.print("[red]choose one target: --base, --commit, --pr, or --staged[/red]")
        raise typer.Exit(1)
    if assess and no_assess:
        console.print("[red]--assess and --no-assess are mutually exclusive[/red]")
        raise typer.Exit(1)
    if post and no_post:
        console.print("[red]--post and --no-post are mutually exclusive[/red]")
        raise typer.Exit(1)
    if post and not pr:
        console.print("[red]--post only applies to --pr reviews[/red]")
        raise typer.Exit(1)
    if as_json and sarif:
        console.print("[red]--json and --sarif are mutually exclusive[/red]")
        raise typer.Exit(1)
    fail = (fail_on or "").strip().lower()
    if fail and fail not in _FAIL_VALUES:
        console.print("[red]--fail-on must be blocker, major, minor, nit, or never[/red]")
        raise typer.Exit(1)
    if max_findings is not None and max_findings < 1:
        console.print("[red]--max-findings must be at least 1[/red]")
        raise typer.Exit(1)
    if context_lines is not None and context_lines < 0:
        console.print("[red]--context-lines must be zero or greater[/red]")
        raise typer.Exit(1)

    opts = ReviewOptions(
        max_findings=max_findings,
        context_lines=context_lines,
        fail_on=fail,
        assess="always" if assess else "never" if no_assess else "",
        intent=intent,
        model=model,
        post="always" if post else "never" if no_post else "auto",
        session="review",
        trust_project=trust_project_rules,
        disable_rules=tuple(disable or ()),
        severity_overrides=tuple(severity or ()),
    )

    if explain or check_config:
        pack = load_review_pack(
            cwd="",
            trust_project=trust_project_rules,
            overrides=opts.pack_overrides(),
        )
        if explain:
            typer.echo(pack.explain())
        if check_config:
            issues = pack.check()
            for level, message in issues:
                color = "red" if level == "error" else "yellow"
                console.print(f"[{color}]{level}[/{color}]: {message}")
            if not issues:
                console.print("[green]Review pack OK[/green]")
            elif not any(level == "error" for level, _ in issues):
                console.print("[green]Review pack OK (warnings only)[/green]")
            if any(level == "error" for level, _ in issues):
                raise typer.Exit(1)
        return

    cfg = _cfg()
    try:
        report = review(
            cwd="",
            staged=staged,
            base=base,
            commit=commit,
            pr=pr,
            path=path,
            options=opts,
            cfg=cfg,
        )
    except ReviewError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if as_json:
        typer.echo(report.to_json())
    elif sarif:
        typer.echo(json.dumps(report.to_sarif(), indent=2))
    else:
        console.print(Markdown(report.to_markdown()))
        if report.post_note:
            console.print(f"[yellow]{report.post_note}[/yellow]")

    if post and not report.posted_url:
        if not as_json and not sarif:
            console.print(f"[red]{report.post_note or 'review was not posted'}[/red]")
        raise typer.Exit(1)

    if fail_on_threshold(report.findings, fail or report.fail_on):
        raise typer.Exit(1)
