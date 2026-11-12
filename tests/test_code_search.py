"""Approval-free code search and globbing (#398).

The semantic contract is the Python walker: .gitignore, hidden paths, symlinks,
binary files, protected paths, result caps, and truncation must all behave the
same when rg or grep is the accelerator.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import sk.agent as agent_mod
import sk.checkpoints as checkpoints
import sk.hooks as hooks
import sk.store as store
import sk.tools.search as search_mod
from sk.cli.approvers import _make_approver
from sk.tools import (
    APPROVAL_TOOLS,
    PLAN_DENIED_TOOLS,
    READONLY_DENIED_TOOLS,
    TOOLS_SCHEMA,
    dispatch_tool,
)
from sk.tools.search import tool_glob_files, tool_search_code


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _force_backend(monkeypatch, backend: str) -> None:
    """Pick one search backend without touching the real PATH."""
    real = shutil.which
    if backend == "rg" and not real("rg"):
        pytest.skip("rg is not installed")
    if backend == "grep" and not real("grep"):
        pytest.skip("grep is not installed")

    def fake_which(name: str) -> str | None:
        if backend == "rg" and name == "rg":
            return real("rg")
        if backend == "grep" and name == "grep":
            return real("grep")
        return None

    monkeypatch.setattr(search_mod.shutil, "which", fake_which)


@pytest.mark.parametrize("backend", ["rg", "grep", "python"])
def test_search_backends_share_ignore_binary_symlink_guards(tmp_path, monkeypatch, backend):
    _force_backend(monkeypatch, backend)
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("needle outside\n")

    _write(repo, "src/keep.py", "def needle():\n    return 'found'\n")
    _write(repo, "ignored.txt", "needle ignored\n")
    _write(repo, "drop.log", "needle drop\n")
    _write(repo, "keep.log", "needle keep\n")
    _write(repo, "build/generated.py", "needle generated\n")
    _write(repo, "sub/local.txt", "needle local\n")
    _write(repo, "sub/kept.py", "needle nested\n")
    (repo / ".gitignore").write_text("ignored.txt\nbuild/\n*.log\n!keep.log\n")
    (repo / "sub" / ".gitignore").write_text("local.txt\n")
    _write(repo, ".hidden/secret.py", "needle hidden\n")
    (repo / "binary.bin").write_bytes(b"needle\x00binary\n")
    (repo / "link.py").symlink_to(outside)

    out = tool_search_code("needle", str(repo))

    assert f"{repo / 'src' / 'keep.py'}:1: def needle():" in out
    assert f"{repo / 'sub' / 'kept.py'}:1: needle nested" in out
    assert f"{repo / 'keep.log'}:1: needle keep" in out
    assert "ignored.txt" not in out
    assert "drop.log" not in out
    assert "generated.py" not in out
    assert "local.txt" not in out
    assert ".hidden" not in out
    assert "binary.bin" not in out
    assert "link.py" not in out
    assert "outside.py" not in out


@pytest.mark.parametrize("backend", ["rg", "grep", "python"])
def test_invalid_regex_is_explicit_for_every_backend(tmp_path, monkeypatch, backend):
    _force_backend(monkeypatch, backend)
    out = tool_search_code("[", str(tmp_path))
    assert out.lower().startswith("error")
    assert "regex" in out.lower()


def test_grep_error_classifies_bsd_regex_wording():
    out = search_mod._grep_error("grep: brackets ([ ]) not balanced", 2)
    assert "invalid regex" in out
    assert "brackets" in out
    out = search_mod._grep_error("grep: Permission denied", 2)
    assert "search failed" in out


def test_search_result_cap_and_snippet_cap(tmp_path, monkeypatch):
    _force_backend(monkeypatch, "python")
    for name in ("a.py", "b.py", "c.py"):
        _write(tmp_path, name, "needle\n")
    out = tool_search_code("needle", str(tmp_path), max_results=2)
    matches = [line for line in out.splitlines() if line.startswith(str(tmp_path))]
    assert len(matches) == 2
    assert "truncated" in out.lower()
    assert "increase max_results" in out

    long = _write(tmp_path, "long.py", "needle " + ("x" * 300) + "\n")
    out = tool_search_code("needle", str(long))
    snippet = out.split(": ", 1)[1]
    assert len(snippet) <= search_mod.MAX_SNIPPET_CHARS
    assert snippet.endswith("...")


def test_search_argument_validation(tmp_path):
    assert "pattern must not be empty" in tool_search_code("", str(tmp_path))
    assert "single-line" in tool_search_code("a\nb", str(tmp_path))
    assert "max_results" in tool_search_code("x", str(tmp_path), max_results="nope")
    assert "max_results" in tool_search_code("x", str(tmp_path), max_results=0)


def test_search_hard_cap_and_no_matches(tmp_path, monkeypatch):
    _force_backend(monkeypatch, "python")
    for index in range(5):
        _write(tmp_path, f"f{index}.py", "needle\n")
    monkeypatch.setattr(search_mod, "MAX_SEARCH_RESULTS_HARD", 3)
    out = tool_search_code("needle", str(tmp_path), max_results=999)
    matches = [line for line in out.splitlines() if line.startswith(str(tmp_path))]
    assert len(matches) == 3
    assert "truncated" in out.lower()
    assert tool_search_code("absent", str(tmp_path)) == "(no matches)"


def test_path_traversal_glob_and_protected_paths(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = _write(tmp_path, "outside.py", "needle outside\n")
    (repo / "link.py").symlink_to(outside)
    (repo / "ssh-link").symlink_to(Path.home() / ".ssh")

    out = tool_search_code("needle", str(repo))
    assert "outside.py" not in out
    assert "ssh-link" not in out

    assert tool_glob_files("../outside.py", str(repo)) == "(no matches)"
    assert "outside.py" not in tool_glob_files("**/*.py", str(repo))
    assert "link.py" not in tool_glob_files("**/*.py", str(repo))

    blocked = Path.home() / ".ssh"
    assert "blocked" in tool_search_code("x", str(blocked)).lower()
    assert "blocked" in tool_glob_files("*", str(blocked)).lower()


def test_glob_files_sorted_ignores_and_returns_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "z.py", "")
    _write(repo, "a.py", "")
    _write(repo, "sub/b.py", "")
    _write(repo, "ignored.py", "")
    _write(repo, ".hidden/secret.py", "")
    (repo / "link.py").symlink_to(repo / "a.py")
    (repo / ".gitignore").write_text("ignored.py\n")

    out = tool_glob_files("**/*.py", str(repo))
    paths = out.splitlines()
    assert paths == sorted(paths)
    assert str(repo / "a.py") in paths
    assert str(repo / "sub" / "b.py") in paths
    assert all("ignored.py" not in path for path in paths)
    assert all(".hidden" not in path for path in paths)
    assert all("link.py" not in path for path in paths)
    assert tool_glob_files("*.rs", str(repo)) == "(no matches)"

    single = _write(repo, "single.py", "")
    assert tool_glob_files("*.py", str(single)) == str(single)
    assert tool_glob_files("*.rs", str(single)) == "(no matches)"


def test_glob_files_cap_is_explicit(tmp_path):
    for index in range(search_mod.MAX_GLOB_RESULTS + 5):
        _write(tmp_path, f"f{index:03}.txt", "")
    out = tool_glob_files("*.txt", str(tmp_path))
    lines = out.splitlines()
    paths = lines[:-1]
    assert len(paths) == search_mod.MAX_GLOB_RESULTS
    assert paths == sorted(paths)
    assert "truncated" in lines[-1]
    assert f"of {search_mod.MAX_GLOB_RESULTS + 5} paths" in lines[-1]


def test_schema_dispatch_and_approval_free(tmp_path):
    names = {entry["function"]["name"] for entry in TOOLS_SCHEMA}
    assert {"search_code", "glob_files"} <= names
    assert len(names) == 27
    assert not {"search_code", "glob_files"} & APPROVAL_TOOLS
    assert not {"search_code", "glob_files"} & PLAN_DENIED_TOOLS
    assert not {"search_code", "glob_files"} & READONLY_DENIED_TOOLS

    kept = _write(tmp_path, "kept.py", "needle\n")
    out = dispatch_tool(
        "search_code",
        {"pattern": "needle", "path": str(tmp_path), "glob": "*.py", "max_results": 1},
    )
    assert f"{kept}:1:" in out
    assert "missing required params" in dispatch_tool("search_code", {})

    out = dispatch_tool("glob_files", {"pattern": "*.py", "path": str(tmp_path)})
    assert out == str(kept)

    approve = _make_approver(False)
    assert approve("search_code", {"pattern": "needle"}) is True
    assert approve("glob_files", {"pattern": "*"}) is True


def test_gated_dispatch_is_audited_without_requesting_approval(tmp_path, monkeypatch):
    _write(tmp_path, "kept.py", "needle\n")
    audit: list[tuple[tuple, dict]] = []

    monkeypatch.setattr(hooks, "pre_tool_use", lambda *args, **kwargs: (True, ""))
    monkeypatch.setattr(hooks, "post_tool_use", lambda *args, **kwargs: None)
    monkeypatch.setattr(checkpoints, "snapshot_before", lambda *args, **kwargs: None)
    monkeypatch.setattr(store, "log_tool_run", lambda *args, **kwargs: audit.append((args, kwargs)))

    def no_approval(*args, **kwargs):
        raise AssertionError("search tools must never request approval")

    args = {"pattern": "needle", "path": str(tmp_path)}
    out, approved = agent_mod._gated_dispatch(
        "search_code", args, approve=no_approval, session="search-audit"
    )
    assert approved is True
    assert "needle" in out
    assert audit
    assert audit[-1][0][:2] == ("search-audit", "search_code")
    assert audit[-1][1]["approved"] is True
    assert audit[-1][1]["ok"] is True

    out, approved = agent_mod._gated_dispatch(
        "glob_files",
        {"pattern": "*.py", "path": str(tmp_path)},
        approve=no_approval,
        session="search-audit",
        read_only=True,
        plan_mode=True,
    )
    assert approved is True
    assert "kept.py" in out
