"""CLI command: sk pr — prepare a pull request from the working tree.

The ship rung of the developer ladder: turn the branch into a reviewable pull
request, without pushing anything behind the user's back.

The title and body are **derived**, not written: they come from the branch's
commits and the diff, so they cannot claim work that did not happen. Pass
``--title``/``--body`` to write your own.

Nothing is pushed or opened without approval.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import typer

from ..base import app, console

MAX_TITLE = 72


def _root() -> str:
    return os.getcwd()


def _git(*args: str, timeout: float = 30.0) -> tuple[int, str, str]:
    from sk.tools.git import run_git

    return run_git(_root(), *args, timeout=timeout)


def _default_base() -> str:
    """The branch a pull request would merge into: the repo's default branch."""
    code, out, _ = _git("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if code == 0 and out.strip():
        return out.strip().split("/")[-1]
    for candidate in ("main", "master"):
        code, _, _ = _git("rev-parse", "--verify", candidate)
        if code == 0:
            return candidate
    return "main"


def _branch() -> str:
    code, out, _ = _git("rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if code == 0 else ""


def _commits(base: str) -> list[str]:
    code, out, _ = _git("log", "--pretty=%s", f"{base}..HEAD")
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _diff_stat(base: str, branch: str) -> str:
    """The diff against the merge base, or the working tree when nothing is
    committed yet."""
    if branch and branch != base:
        code, out, _ = _git("diff", "--stat", f"{base}...HEAD")
        if code == 0 and out.strip():
            return out.strip()
    code, out, _ = _git("diff", "--stat", "HEAD")
    if code == 0 and out.strip():
        return out.strip()
    code, out, _ = _git("diff", "--stat")
    return out.strip() if code == 0 else ""


def _derive_title(commits: list[str], branch: str) -> str:
    """The title: the first commit's subject, or the branch name."""
    if commits:
        subject = commits[0]
        return subject if len(subject) <= MAX_TITLE else subject[: MAX_TITLE - 1].rstrip() + "…"
    return f"Update {branch}" if branch else "Update"


def _derive_body(commits: list[str], stat: str) -> str:
    """The body: what was committed, and what changed. Nothing invented."""
    parts: list[str] = []
    if commits:
        parts.append("**Commits**")
        parts.extend(f"- {c}" for c in commits)
        parts.append("")
    if stat:
        parts.append("**Files changed**")
        parts.append("```")
        parts.append(stat)
        parts.append("```")
    return "\n".join(parts).strip() or "No changes to describe."


def _is_protected(branch: str) -> bool:
    """The same protected-branch rule the git tools follow."""
    from sk.tools.git import _is_protected as protected

    return protected(branch)


def _gh_available() -> tuple[bool, str]:
    """Is `gh` installed and authenticated? Returns (available, note)."""
    gh = shutil.which("gh")
    if not gh:
        return False, "gh is not installed"
    try:
        result = subprocess.run(
            [gh, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as e:  # noqa: BLE001 - a missing gh is a message, not a crash
        return False, f"gh auth status failed: {e}"
    if result.returncode != 0:
        return False, "gh is installed but not authenticated"
    return True, "gh"


@app.command("pr")
def pr_cmd(
    base: str = typer.Option("", "--base", help="Base branch (default: the repo's default branch)"),
    title: str = typer.Option("", "--title", help="PR title (default: derived from the commits)"),
    body: str = typer.Option(
        "", "--body", help="PR body (default: derived from the commits and diff)"
    ),
    remote: str = typer.Option("origin", "--remote", help="Remote to push to"),
    push: bool = typer.Option(False, "--push", help="Push the branch and open the pull request"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmations"),
    as_json: bool = typer.Option(False, "--json", help="Emit a machine-readable report"),
):
    """Prepare a pull request from the working tree.

    The title and body are derived from the branch's commits and the diff — they
    cannot claim work that did not happen. With `--push`, the branch is pushed
    and the pull request is opened through `gh`, both after a confirmation.

    Without `--push`, nothing leaves the machine: the prepared pull request is
    printed, and the commands it would run are shown.
    """
    code, _, err = _git("rev-parse", "--git-dir")
    if code != 0:
        console.print(f"[red]Not a git repository: {(err or '').strip()[:200]}[/red]")
        raise typer.Exit(1)

    branch = _branch()
    base = base or _default_base()
    commits = _commits(base)
    stat = _diff_stat(base, branch)
    pr_title = title.strip() or _derive_title(commits, branch)
    pr_body = body.strip() or _derive_body(commits, stat)

    pushed = False
    opened = ""
    notes: list[str] = []

    if push:
        available, note = _gh_available()
        declined = False
        if _is_protected(branch):
            # The same rule the git tools follow: a protected branch is not the
            # agent's to push. Create a feature branch and try again.
            notes.append(
                f"not pushed: '{branch}' is a protected branch — create a feature branch first"
            )
        if not yes:
            try:
                # In JSON mode the prompt goes to stderr: stdout is the data
                # channel.
                confirmed = typer.confirm(
                    f"Push '{branch}' to '{remote}' and open the pull request against '{base}'?",
                    default=False,
                    err=as_json,
                )
            except (EOFError, KeyboardInterrupt, typer.Abort):
                # No input available (a pipe, a CI run): treat it as declined.
                confirmed = False
            declined = not confirmed
        if declined:
            notes.append("not pushed: declined")
        elif any("protected branch" in n for n in notes):
            pass
        else:
            push_code, push_out, push_err = _git(
                "push", "--set-upstream", remote, branch, timeout=120.0
            )
            if push_code != 0:
                notes.append(f"push failed: {(push_err or push_out).strip()[:400]}")
            else:
                pushed = True
                gh = shutil.which("gh")
                if not available or not gh:
                    # The push stands on its own; only opening the pull
                    # request needs gh.
                    notes.append(f"pushed, but the pull request was not opened: {note}")
                else:
                    create = subprocess.run(
                        [
                            gh,
                            "pr",
                            "create",
                            "--base",
                            base,
                            "--title",
                            pr_title,
                            "--body",
                            pr_body,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=120,
                        check=False,
                    )
                    if create.returncode == 0:
                        opened = (create.stdout or "").strip()
                    else:
                        notes.append(
                            "pushed, but the pull request was not opened: "
                            f"{(create.stderr or create.stdout).strip()[:400]}"
                        )

    if as_json:
        typer.echo(
            json.dumps(
                {
                    "base": base,
                    "branch": branch,
                    "title": pr_title,
                    "body": pr_body,
                    "commits": commits,
                    "pushed": pushed,
                    "pull_request": opened,
                    "notes": notes,
                },
                indent=2,
            )
        )
        raise typer.Exit(0 if not notes or pushed else 1)

    console.print(f"[cyan]{pr_title}[/cyan]")
    console.print(f"[dim]{base} ← {branch}[/dim]")
    console.print()
    console.print(pr_body)
    if pushed:
        if opened:
            console.print(f"[green]{opened}[/green]")
        else:
            console.print("[green]pushed[/green]")
    else:
        console.print(
            f"[dim]not pushed. To open it: git push --set-upstream {remote} {branch} "
            f"&& gh pr create --base {base}[/dim]"
        )
    for note in notes:
        console.print(f"[yellow]{note}[/yellow]")
    raise typer.Exit(0 if (not notes or pushed) else 1)
