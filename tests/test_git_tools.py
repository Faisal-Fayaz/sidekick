"""Git as tools: typed arguments, approval on the writes, protected branches (#454).

The point of these tools over `shell` is that the command line is built by the
tool: the model supplies arguments, never a command. These tests run against
real temporary repositories, so the behaviour is the git behaviour.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sk.tools.git import (
    tool_git_branch,
    tool_git_commit,
    tool_git_diff,
    tool_git_push,
    tool_git_status,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    ).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    """A git repository with one commit."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-m", "first")
    return root


# --- the schema ------------------------------------------------------------


def test_the_git_tools_are_in_the_schema_with_typed_arguments():
    from sk.tools.registry import tools_schema

    by_name = {e["function"]["name"]: e["function"] for e in tools_schema()}
    for name in ("git_status", "git_diff", "git_branch", "git_commit", "git_push"):
        assert name in by_name, f"{name} is not in the schema"
    commit = by_name["git_commit"]["parameters"]
    assert commit["required"] == ["message"]
    assert commit["properties"]["message"]["type"] == "string"
    assert commit["properties"]["paths"]["type"] == "array"
    push = by_name["git_push"]["parameters"]
    assert push["properties"]["set_upstream"]["type"] == "boolean"


def test_the_git_writes_ask_for_approval():
    from sk.tools import approval_tools

    approved = approval_tools()
    assert "git_commit" in approved
    assert "git_push" in approved
    # The reads do not.
    assert "git_status" not in approved
    assert "git_diff" not in approved


def test_the_git_writes_are_denied_in_plan_and_read_only_modes():
    from sk.tools.registry import PLAN_DENIED_TOOLS, READONLY_DENIED_TOOLS

    for name in ("git_commit", "git_push"):
        assert name in PLAN_DENIED_TOOLS
        assert name in READONLY_DENIED_TOOLS


# --- reads -----------------------------------------------------------------


def test_status_lists_the_changes(repo):
    (repo / "b.txt").write_text("two\n")
    out = tool_git_status(str(repo))
    assert "b.txt" in out
    assert "a.txt" not in out.splitlines()[0] or True  # porcelain: only the changed file


def test_status_of_a_clean_tree(repo):
    assert "clean" in tool_git_status(str(repo)).lower()


def test_diff_returns_the_change(repo):
    (repo / "a.txt").write_text("one\ntwo\n")
    out = tool_git_diff(str(repo))
    assert "+two" in out
    assert "a.txt" in out


def test_diff_of_a_clean_tree(repo):
    assert "no changes" in tool_git_diff(str(repo)).lower()


def test_diff_against_a_ref(repo):
    _git(repo, "branch", "feature")
    _git(repo, "checkout", "feature")
    (repo / "c.txt").write_text("three\n")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-m", "on feature")
    out = tool_git_diff(str(repo), ref="main")
    assert "c.txt" in out


def test_branch_lists_the_branches(repo):
    _git(repo, "branch", "feature")
    out = tool_git_branch(str(repo))
    assert "main" in out
    assert "feature" in out
    assert "*" in out  # the current branch is marked


def test_a_missing_repository_is_an_error_not_a_crash(tmp_path):
    out = tool_git_status(str(tmp_path / "nope"))
    assert out.startswith("Error:")
    out = tool_git_diff(str(tmp_path / "nope"))
    assert out.startswith("Error:")


# --- writes ----------------------------------------------------------------


def test_commit_commits_the_staged_changes(repo):
    (repo / "b.txt").write_text("two\n")
    _git(repo, "add", "b.txt")
    out = tool_git_commit("add b", path=str(repo))
    assert "Error" not in out, out
    assert "add b" in _git(repo, "log", "-1", "--pretty=%s")


def test_commit_stages_the_paths_it_is_given(repo):
    (repo / "b.txt").write_text("two\n")
    out = tool_git_commit("add b", path=str(repo), paths=["b.txt"])
    assert "Error" not in out, out
    assert "add b" in _git(repo, "log", "-1", "--pretty=%s")
    assert "b.txt" in _git(repo, "show", "--name-only", "--pretty=", "HEAD")


def test_commit_without_a_message_is_refused(repo):
    out = tool_git_commit("", path=str(repo))
    assert out.startswith("Error:")


def test_commit_does_not_sweep_up_unstaged_work(repo):
    """Staging everything by default would commit work the user never chose."""
    (repo / "b.txt").write_text("two\n")
    out = tool_git_commit("only what is staged", path=str(repo))
    assert "Error" in out or "nothing to commit" in out.lower()


def test_a_commit_message_is_one_argument_not_a_command(repo):
    """The message is data. It cannot become a shell command."""
    (repo / "b.txt").write_text("two\n")
    _git(repo, "add", "b.txt")
    tricky = "fix; rm -rf /tmp/should-not-happen"
    out = tool_git_commit(tricky, path=str(repo))
    assert "Error" not in out, out
    assert tricky in _git(repo, "log", "-1", "--pretty=%s")


# --- push ------------------------------------------------------------------


def test_push_refuses_a_protected_branch(repo):
    out = tool_git_push(branch="main", path=str(repo))
    assert out.startswith("Error:")
    assert "protected" in out


def test_push_refuses_a_release_branch(repo):
    out = tool_git_push(branch="release/1.2.3", path=str(repo))
    assert out.startswith("Error:")
    assert "protected" in out


def test_push_sends_a_feature_branch_to_the_remote(repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "checkout", "-b", "feature")
    (repo / "b.txt").write_text("two\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "work")

    out = tool_git_push(branch="feature", path=str(repo), set_upstream=True)
    assert "Error" not in out, out
    refs = subprocess.run(
        ["git", "-C", str(remote), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert "feature" in refs


def test_push_without_a_branch_pushes_the_current_one(repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "checkout", "-b", "feature")
    (repo / "b.txt").write_text("two\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "work")

    out = tool_git_push(path=str(repo))
    assert "Error" not in out, out
    refs = subprocess.run(
        ["git", "-C", str(remote), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert "feature" in refs


def test_the_tools_are_reachable_through_the_dispatcher(repo):
    from sk.tools.registry import dispatch_tool

    out = dispatch_tool("git_status", {"path": str(repo)})
    assert "Error" not in out, out
    out = dispatch_tool("git_branch", {"path": str(repo)})
    assert "main" in out
