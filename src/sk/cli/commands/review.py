"""CLI command: sk review — evidence-backed review of a diff or PR."""

from __future__ import annotations

import json

import typer
from rich.markdown import Markdown

from sk.review.engine import ReviewError, ReviewOptions, review
from sk.review.schema import fail_on_threshold

from ..base import _cfg, app, console


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
        "never", "--fail-on", help="Exit 1 at or above: blocker|major|minor|nit|never"
    ),
    model: str = typer.Option("", "--model", help="Override the review model"),
    max_findings: int = typer.Option(25, "--max-findings", help="Cap the finding list"),
    context_lines: int = typer.Option(3, "--context-lines", help="Evidence context window"),
):
    """Review a diff with evidence-backed findings and an advisory worth-it check.

    Defaults to the working tree. Use `--base main` before a PR, `--staged`
    before committing, or `--pr N` to review and (when credentials exist)
    comment on the pull request. Review never edits source files.
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
    fail = (fail_on or "never").strip().lower()
    if fail not in ("blocker", "major", "minor", "nit", "never"):
        console.print("[red]--fail-on must be blocker, major, minor, nit, or never[/red]")
        raise typer.Exit(1)

    cfg = _cfg()
    opts = ReviewOptions(
        max_findings=max(0, int(max_findings)),
        context_lines=max(0, int(context_lines)),
        fail_on=fail,
        assess="always" if assess else "never" if no_assess else "auto",
        intent=intent,
        model=model,
        post="always" if post else "never" if no_post else "auto",
        session="review",
    )
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

    if fail_on_threshold(report.findings, fail):
        raise typer.Exit(1)
