"""Diff target resolution and unified-diff parsing (offline, real git temp repos)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from sk.review.diff import (
    DiffModel,
    FileDiff,
    Hunk,
    collect_diff,
    iter_chunks,
    parse_unified_diff,
    repo_root,
)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(cwd, *args):
    r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=30, cwd=str(cwd))
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("needs git")
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "test@example.com")
    _git(d, "config", "user.name", "Test")
    (d / "app.py").write_text("x = 1\n")
    (d / "keep.txt").write_text("keep\n")
    _git(d, "add", ".")
    _git(d, "commit", "-qm", "initial")
    return d


@needs_git
def test_worktree_diff_has_hunks_and_changed_lines(repo):
    (repo / "app.py").write_text("x = 1\ny = 2\n")
    d = collect_diff(cwd=str(repo))
    assert d.target == "worktree"
    assert d.ref == "HEAD"
    f = {x.path: x for x in d.files}["app.py"]
    assert f.additions == 1
    assert f.deletions == 0
    assert f.changed_new_lines() == {2}
    assert any(h.new_start == 1 for h in f.hunks)
    assert "y = 2" in d.raw


@needs_git
def test_staged_diff_excludes_unstaged(repo):
    (repo / "app.py").write_text("x = 1\ny = 2\n")
    _git(repo, "add", "app.py")
    (repo / "keep.txt").write_text("changed\n")
    d = collect_diff(cwd=str(repo), staged=True)
    assert d.target == "staged"
    assert [f.path for f in d.files] == ["app.py"]
    assert "keep.txt" not in d.raw


@needs_git
def test_base_uses_merge_base_and_ignores_unrelated_main_advance(repo):
    _git(repo, "checkout", "-qb", "feature")
    (repo / "app.py").write_text("x = 2\n")
    _git(repo, "commit", "-qam", "feature change")
    _git(repo, "checkout", "-q", "main")
    (repo / "keep.txt").write_text("main change\n")
    _git(repo, "commit", "-qam", "main change")
    _git(repo, "checkout", "-q", "feature")
    d = collect_diff(cwd=str(repo), base="main")
    assert d.ref == "main...HEAD"
    assert [f.path for f in d.files] == ["app.py"]
    assert "+x = 2" in d.raw


@needs_git
def test_commit_target_uses_parent(repo):
    (repo / "app.py").write_text("x = 3\n")
    _git(repo, "commit", "-qam", "change x")
    sha = _git(repo, "rev-parse", "HEAD")
    d = collect_diff(cwd=str(repo), commit=sha)
    assert any(f.path == "app.py" for f in d.files)
    assert "+x = 3" in d.raw


@needs_git
def test_path_filter(repo):
    (repo / "app.py").write_text("x = 9\n")
    (repo / "keep.txt").write_text("new\n")
    d = collect_diff(cwd=str(repo), path="keep.txt")
    assert [f.path for f in d.files] == ["keep.txt"]


@needs_git
def test_generated_and_lockfiles_are_skipped(repo):
    (repo / "uv.lock").write_text("lock\n")
    (repo / "dist").mkdir()
    (repo / "dist" / "bundle.js").write_text("x\n")
    (repo / "app.py").write_text("x = 4\n")
    _git(repo, "add", "-A")
    d = collect_diff(cwd=str(repo))
    paths = {f.path for f in d.files}
    assert "app.py" in paths
    assert "uv.lock" not in paths
    assert any(s.path == "uv.lock" for s in d.skipped)
    assert any(s.path == "dist/bundle.js" for s in d.skipped)


@needs_git
def test_binary_file_is_skipped(repo):
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02")
    _git(repo, "add", "-A")
    d = collect_diff(cwd=str(repo))
    assert any(s.path == "blob.bin" and "binary" in s.reason for s in d.skipped)


@needs_git
def test_non_git_raises(tmp_path):
    with pytest.raises(Exception):
        repo_root(str(tmp_path))


def test_parse_unified_diff_handles_rename_and_added_file():
    raw = """diff --git a/old.py b/new.py
similarity index 90%
rename from old.py
rename to new.py
--- a/old.py
+++ b/new.py
@@ -1 +1,2 @@
 x = 1
+y = 2
"""
    files, skipped = parse_unified_diff(raw)
    assert skipped == ()
    f = files[0]
    assert f.path == "new.py"
    assert f.old_path == "old.py"
    assert f.status == "R"
    assert f.changed_new_lines() == {2}


def test_parse_unified_diff_empty():
    files, skipped = parse_unified_diff("")
    assert files == ()
    assert skipped == ()


def test_iter_chunks_keeps_hunk_headers():
    d = DiffModel(
        target="worktree",
        repo_root="/tmp",
        ref="HEAD",
        files=(
            FileDiff(
                path="a.py",
                hunks=(
                    Hunk(
                        old_start=1,
                        old_count=1,
                        new_start=7,
                        new_count=2,
                        header="@@ -1,1 +7,2 @@",
                        lines=(" x = 1", "+y = 2"),
                    ),
                ),
            ),
        ),
    )
    chunks = list(iter_chunks(d))
    assert chunks
    assert "@@ -1,1 +7,2 @@" in chunks[0]
    assert "+y = 2" in chunks[0]


@needs_git
def test_root_commit_target(tmp_path):
    d = tmp_path / "root"
    d.mkdir()
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "T")
    (d / "a.py").write_text("x = 1\n")
    _git(d, "add", ".")
    _git(d, "commit", "-qm", "root")
    sha = _git(d, "rev-parse", "HEAD")
    out = collect_diff(cwd=str(d), commit=sha)
    assert [f.path for f in out.files] == ["a.py"]
    assert "+x = 1" in out.raw


def test_no_newline_marker_does_not_shift_line_numbers():
    raw = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
 x = 1
-y = 2
+y = 3
\\ No newline at end of file
"""
    files, _ = parse_unified_diff(raw)
    assert files[0].changed_new_lines() == {2}


@needs_git
def test_pr_diff_text_path_uses_supplied_meta(repo):
    raw = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1,2 @@
 x = 1
+y = 2
"""
    d = collect_diff(cwd=str(repo), pr=7, diff_text=raw, pr_meta={"title": "T"})
    assert d.target == "pr:7"
    assert d.ref == "PR #7"
    assert d.meta["title"] == "T"
    assert d.files[0].changed_new_lines() == {2}
    assert d.files[0].additions == 1
