"""Git as tools: typed arguments, parsed output, approval on the writes (#454).

The command line is built here, from typed arguments. The model never
supplies a command string, so there is nothing for it to smuggle into the
shell — that is the whole difference between `git_commit` and a `shell` call
with a `git commit` in it.

Reads are free. Committing and pushing ask, exactly as the shell does, and
both are audited. `git_push` refuses a protected branch: the branch a
release ships from is not the agent's to push.

Nothing here raises: a git failure is a message the model can read and act on.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Branches the agent may not push to. `main` and `master` are the obvious
# ones; the release convention (`release/*`) is protected too, because a
# pushed release branch starts a publish.
PROTECTED_BRANCHES = ("main", "master")

MAX_DIFF_CHARS = 20000
MAX_STATUS_CHARS = 8000


def _root(path: str = "") -> str:
    """The directory the git command runs in."""
    if path:
        try:
            return str(Path(path).expanduser().resolve())
        except Exception:
            return path
    return os.getcwd()


def run_git(root: str, *args: str, timeout: float = 30.0) -> tuple[int, str, str]:
    """Run git with argv. Never raises. Returns (code, stdout, stderr).

    Public so the CLI can prepare a pull request with the same git the tools
    use — one implementation, one set of rules.
    """
    return _git(root, *args, timeout=timeout)


def _git(root: str, *args: str, timeout: float = 30.0) -> tuple[int, str, str]:
    """Run git with argv. Never raises. Returns (code, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return 127, "", "git is not installed"
    except subprocess.TimeoutExpired:
        return 124, "", f"git timed out after {timeout:g}s"
    except Exception as e:  # noqa: BLE001 - a tool failure is a message
        return 1, "", f"{type(e).__name__}: {e}"
    return result.returncode, result.stdout or "", result.stderr or ""


def _current_branch(root: str) -> str:
    code, out, _ = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if code == 0 else ""


def _is_protected(branch: str) -> bool:
    name = str(branch or "").strip()
    if not name:
        return False
    if name in PROTECTED_BRANCHES:
        return True
    return name.startswith("release/")


def tool_git_status(path: str = ".") -> str:
    """The working tree status, in porcelain form: one line per change."""
    root = _root(path)
    code, out, err = _git(root, "status", "--porcelain=v1", "--branch")
    if code != 0:
        return f"Error: git status failed: {(err or out).strip()[:400]}"
    branch = ""
    changes: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        if line.startswith("## "):
            branch = line[3:].strip()
            continue
        changes.append(line.rstrip())
    lines = [f"branch: {branch}"] if branch else []
    lines.extend(changes)
    if not changes:
        lines.append("(clean working tree)")
    text = "\n".join(lines)
    if len(text) > MAX_STATUS_CHARS:
        text = text[:MAX_STATUS_CHARS] + "\n... [truncated]"
    return text


def tool_git_diff(path: str = "", staged: bool = False, ref: str = "") -> str:
    """The diff: the working tree by default, the index with staged, or a ref."""
    root = _root(path)
    args = ["diff"]
    if staged:
        args.append("--staged")
    if ref:
        args.append(str(ref))
    if path and not ref:
        args.extend(["--", str(path)])
    code, out, err = _git(root, *args)
    if code != 0:
        return f"Error: git diff failed: {(err or out).strip()[:400]}"
    text = out
    if len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS] + "\n... [truncated]"
    return text.strip() or "(no changes)"


def tool_git_branch(path: str = "") -> str:
    """The branches: the current one, and the local ones."""
    root = _root(path)
    code, out, err = _git(root, "branch", "--format=%(refname:short) %(HEAD)")
    if code != 0:
        return f"Error: git branch failed: {(err or out).strip()[:400]}"
    lines = [line for line in out.splitlines() if line.strip()]
    current = _current_branch(root)
    rows = []
    for line in lines:
        name, _, marker = line.partition(" ")
        rows.append(f"{'*' if marker.strip() else ' '} {name}")
    if current and not any(r.startswith("*") for r in rows):
        rows.insert(0, f"* {current}")
    return "\n".join(rows) or "(no branches)"


def tool_git_commit(message: str, path: str = "", paths: list | None = None) -> str:
    """Commit the staged changes, or the given paths.

    With `paths`, those files are staged first. Without, only what is already
    staged is committed — staging everything by default would sweep up work the
    user never chose to commit.
    """
    root = _root(path)
    text = str(message or "").strip()
    if not text:
        return "Error: a commit needs a message."
    targets = [str(p) for p in (paths or []) if str(p).strip()]
    if targets:
        code, _, err = _git(root, "add", "--", *targets)
        if code != 0:
            return f"Error: git add failed: {err.strip()[:400]}"
    code, out, err = _git(root, "commit", "-m", text, "--")
    if code != 0:
        detail = (err or out).strip()[:600]
        return f"Error: git commit failed: {detail}"
    return (out or "").strip() or "Committed."


def tool_git_push(
    remote: str = "origin",
    branch: str = "",
    path: str = "",
    set_upstream: bool = False,
) -> str:
    """Push a branch to a remote. Refuses a protected branch."""
    root = _root(path)
    target = str(remote or "origin").strip() or "origin"
    name = str(branch or "").strip() or _current_branch(root)
    if not name:
        return "Error: no branch to push (not a git repository, or no commits yet)."
    if _is_protected(name):
        return (
            f"Error: refusing to push '{name}'. It is a protected branch — "
            "push a feature branch and open a pull request instead."
        )
    args = ["push"]
    if set_upstream:
        args.append("--set-upstream")
    args.extend([target, name])
    code, out, err = _git(root, *args, timeout=120.0)
    if code != 0:
        return f"Error: git push failed: {(err or out).strip()[:600]}"
    return (out or err or "").strip() or f"Pushed {name} to {target}."
