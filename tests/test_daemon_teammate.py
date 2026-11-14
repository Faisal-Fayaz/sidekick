"""The daemon as a teammate (#470): grounded, and quiet.

Two properties, and they pull against each other:

- **Grounded** — a nudge names the branch, the paths that changed, and how far
  the branch is from its upstream. It never narrates.
- **Quiet** — the same repository in the same state says nothing the second
  time. A teammate that interrupts with nothing is worse than no teammate.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import sk.daemon as daemon
from sk.brief import format_brief_text, git_snapshot


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    ).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-m", "first")
    return root


# --- the snapshot ----------------------------------------------------------


def test_the_snapshot_names_the_changed_files(repo):
    (repo / "b.txt").write_text("two\n")
    (repo / "a.txt").write_text("changed\n")
    snapshot = git_snapshot(str(repo))
    assert snapshot["exists"] is True
    assert snapshot["branch"] == "main"
    assert set(snapshot["files"]) == {"a.txt", "b.txt"}
    assert snapshot["changed"] == 2


def test_the_snapshot_reports_the_head(repo):
    snapshot = git_snapshot(str(repo))
    assert snapshot["head"]
    assert len(snapshot["head"]) >= 7  # a short hash


def test_the_snapshot_of_a_missing_path_is_not_an_error(tmp_path):
    snapshot = git_snapshot(str(tmp_path / "nope"))
    assert snapshot["exists"] is False


# --- the fingerprint -------------------------------------------------------


def test_the_fingerprint_moves_when_the_repository_moves(repo):
    before = daemon.repo_fingerprint(git_snapshot(str(repo)))
    (repo / "a.txt").write_text("changed\n")
    after = daemon.repo_fingerprint(git_snapshot(str(repo)))
    assert before != after


def test_the_fingerprint_is_stable_for_the_same_state(repo):
    first = daemon.repo_fingerprint(git_snapshot(str(repo)))
    second = daemon.repo_fingerprint(git_snapshot(str(repo)))
    assert first == second


# --- the watch -------------------------------------------------------------


def test_the_watch_names_the_branch_and_the_files(repo):
    (repo / "b.txt").write_text("two\n")
    nudges, seen = daemon.repo_watch([str(repo)], {})
    assert nudges, "a changed repository said nothing"
    nudge = nudges[0]
    assert "main" in nudge
    assert "b.txt" in nudge
    assert seen[str(repo)]


def test_the_watch_is_quiet_when_nothing_changed(repo):
    (repo / "b.txt").write_text("two\n")
    nudges, seen = daemon.repo_watch([str(repo)], {})
    assert nudges
    again, _ = daemon.repo_watch([str(repo)], {"repo_fingerprints": seen})
    assert again == [], "the same state said something twice"


def test_the_watch_says_something_again_when_it_changes(repo):
    (repo / "b.txt").write_text("two\n")
    nudges, seen = daemon.repo_watch([str(repo)], {})
    assert nudges
    (repo / "c.txt").write_text("three\n")
    again, _ = daemon.repo_watch([str(repo)], {"repo_fingerprints": seen})
    assert again, "a new change was not reported"
    assert "c.txt" in again[0]


def test_a_clean_repository_says_nothing(repo):
    nudges, _ = daemon.repo_watch([str(repo)], {})
    assert nudges == []


def test_a_repository_that_became_clean_says_nothing(repo):
    (repo / "b.txt").write_text("two\n")
    nudges, seen = daemon.repo_watch([str(repo)], {})
    assert nudges
    (repo / "b.txt").unlink()
    again, _ = daemon.repo_watch([str(repo)], {"repo_fingerprints": seen})
    assert again == []


def test_the_watch_reports_how_far_the_branch_is_from_its_upstream(repo, tmp_path):
    """A branch that drifted is worth saying out loud."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "main")

    _git(repo, "checkout", "-b", "feature")
    _git(repo, "push", "-u", "origin", "feature")
    (repo / "b.txt").write_text("two\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "work")

    nudges, _ = daemon.repo_watch([str(repo)], {})
    assert nudges, "a branch that drifted from its upstream said nothing"
    assert "ahead" in nudges[0]


def test_the_watch_never_raises_for_a_broken_path(tmp_path):
    nudges, seen = daemon.repo_watch([str(tmp_path / "nope")], {})
    assert nudges == []
    assert isinstance(seen, dict)


# --- the check and the brief ----------------------------------------------


def test_the_check_carries_the_watch(repo):
    (repo / "b.txt").write_text("two\n")
    nudges, state = daemon.check_once({"last_shell_id": 0}, projects=[str(repo)], disk_warn=99)
    assert any("b.txt" in n for n in nudges), nudges
    assert state["repo_fingerprints"]


def test_the_check_is_quiet_on_the_second_run(repo):
    (repo / "b.txt").write_text("two\n")
    nudges, state = daemon.check_once({"last_shell_id": 0}, projects=[str(repo)], disk_warn=99)
    assert any("b.txt" in n for n in nudges)
    nudges2, _ = daemon.check_once(state, projects=[str(repo)], disk_warn=99)
    assert not any("b.txt" in n for n in nudges2), nudges2


def test_the_brief_names_the_changed_files(repo):
    (repo / "b.txt").write_text("two\n")
    text = format_brief_text(
        {
            "when": "now",
            "sysinfo": "",
            "projects": [git_snapshot(str(repo))],
            "memories": [],
            "todos": [],
        }
    )
    assert "b.txt" in text
    assert "main" in text
