"""`sk pr`: prepare a pull request without pushing anything (#453).

The pull request text has to be grounded — derived from the commits and the
diff, so it cannot claim work that did not happen — and nothing may leave the
machine without approval.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sk.cli import app


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    ).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    """A repository with a base branch, a feature branch, and one commit."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-m", "first")
    _git(root, "checkout", "-b", "feature")
    (root / "b.txt").write_text("two\n")
    _git(root, "add", "b.txt")
    _git(root, "commit", "-m", "add the second file")
    return root


def _run(args, cwd: Path):
    import os

    previous = os.getcwd()
    os.chdir(cwd)
    try:
        return CliRunner().invoke(app, ["pr", *args])
    finally:
        os.chdir(previous)


def test_the_command_exists():
    result = CliRunner().invoke(app, ["pr", "--help"])
    assert result.exit_code == 0, result.output
    assert "prepare a pull request" in result.output.lower()


def test_the_title_and_body_are_derived_from_the_commits(repo):
    result = _run(["--json"], repo)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["title"] == "add the second file"
    assert "add the second file" in report["body"]
    assert "b.txt" in report["body"], "the diff is not in the body"
    assert report["commits"] == ["add the second file"]


def test_nothing_is_pushed_without_the_flag(repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))

    result = _run(["--json"], repo)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["pushed"] is False
    refs = subprocess.run(
        ["git", "-C", str(remote), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert "feature" not in refs, "the branch was pushed without --push"


def test_an_explicit_title_and_body_win(repo):
    result = _run(["--title", "my title", "--body", "my body", "--json"], repo)
    report = json.loads(result.stdout)
    assert report["title"] == "my title"
    assert report["body"] == "my body"


def test_it_works_without_a_repository(tmp_path):
    result = _run(["--json"], tmp_path)
    assert result.exit_code == 1, result.output
    assert "Not a git repository" in result.output


def test_a_protected_branch_is_not_pushed(repo, tmp_path, monkeypatch):
    """Pushing main is not the agent's to do — the same rule the git tools
    follow (#454)."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "checkout", "main")

    result = _run(["--push", "--yes", "--json"], repo)
    report = json.loads(result.stdout)
    assert report["pushed"] is False
    assert any("protected branch" in note for note in report["notes"]), report["notes"]
    refs = subprocess.run(
        ["git", "-C", str(remote), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert "main" not in refs, "a protected branch was pushed"


def test_the_base_defaults_to_the_default_branch(repo):
    result = _run(["--json"], repo)
    report = json.loads(result.stdout)
    assert report["base"] in ("main", "master")


def test_a_declined_push_does_not_push(repo, tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))

    import sk.cli.commands.pr as mod

    monkeypatch.setattr(mod, "_gh_available", lambda: (True, "gh"))

    result = _run(["--push", "--json"], repo)  # no --yes: the confirm is declined
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert report["pushed"] is False
    assert any("declined" in note for note in report["notes"]), report["notes"]
    refs = subprocess.run(
        ["git", "-C", str(remote), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert "feature" not in refs, "a declined push still pushed"


def test_a_push_without_gh_still_pushes_and_says_why_it_cannot_open(repo, tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))

    import sk.cli.commands.pr as mod

    monkeypatch.setattr(mod, "_gh_available", lambda: (False, "gh is not installed"))

    result = _run(["--push", "--yes", "--json"], repo)
    report = json.loads(result.stdout)
    assert report["pushed"] is True
    assert any("gh is not installed" in note for note in report["notes"]), report["notes"]
    refs = subprocess.run(
        ["git", "-C", str(remote), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert "feature" in refs
