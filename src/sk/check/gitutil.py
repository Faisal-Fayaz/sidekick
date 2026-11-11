"""Git plumbing for project checks.

Only ``git`` argv is used, never a shell. A non-git directory is still a valid
project root for ``sk check``; only ``--changed`` needs a repository.
"""

from __future__ import annotations

import os
import subprocess


class CheckGitError(RuntimeError):
    """A git command needed for checks failed. The message is safe to show."""


def run_git(root: str, *args: str, timeout: float = 20.0) -> tuple[int, str, str]:
    """Run one git command with argv. Never raises; returns (code, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git", "-C", root or ".", *args],
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout)),
        )
    except Exception as e:
        return 127, "", f"{type(e).__name__}: {e}"
    return result.returncode, result.stdout or "", result.stderr or ""


def project_root(cwd: str = "") -> str:
    """Return the git root for ``cwd``, or the absolute cwd when it is not a repo."""
    base = os.path.abspath(cwd or os.getcwd())
    code, out, _ = run_git(base, "rev-parse", "--show-toplevel")
    if code == 0 and out.strip():
        return out.strip()
    return base


def changed_files(root: str, *, limit: int = 2000) -> tuple[str, ...]:
    """Changed files relative to the root: tracked vs HEAD plus untracked.

    Deleted paths are excluded because a linter cannot read them. A repository
    with no commits yet falls back to every tracked/untracked path, which is the
    honest interpretation of "everything is new". ``-z`` avoids git's C-style
    quoting for non-ASCII paths, which would otherwise never match a glob.
    """
    code, out, err = run_git(root, "diff", "--name-only", "-z", "--diff-filter=ACMR", "HEAD")
    if code != 0:
        # Unborn branch: `git diff HEAD` has nothing to compare. Everything
        # tracked or untracked is new, so list it instead.
        code, out, err = run_git(
            root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
        )
        if code != 0:
            raise CheckGitError(f"could not list changed files: {err.strip() or 'git failed'}")

    paths = {item.replace("\\", "/") for item in out.split("\0") if item}

    code, out, _ = run_git(root, "ls-files", "-z", "--others", "--exclude-standard")
    if code == 0:
        paths.update(item.replace("\\", "/") for item in out.split("\0") if item)

    return tuple(sorted(paths)[: max(1, int(limit))])
