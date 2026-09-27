"""Working-tree git diff for /diff and /review. Read-only, never raises."""

from __future__ import annotations

import os
import subprocess


def _run(argv: list[str], cwd: str, timeout: int = 15) -> tuple[bool, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode == 0, (r.stdout or "").strip()
    except Exception as e:
        return False, f"Error: {e}"


def git_diff_text(cwd: str = "", base: str = "", cap: int = 8000) -> str:
    """stat + capped unified diff vs base (default HEAD). Human text, never raises."""
    cwd = cwd or os.getcwd()
    ok, _ = _run(["git", "rev-parse", "--show-toplevel"], cwd)
    if not ok:
        return f"_(not a git repo: {cwd})_"
    ref = (base or "").strip()
    if not ref:
        ok, _ = _run(["git", "rev-parse", "--verify", "HEAD", "--quiet"], cwd)
        ref = "HEAD" if ok else ""
    if ref:
        ok, stat = _run(["git", "diff", ref, "--stat"], cwd)
        if not ok:
            return f"_(could not diff vs {ref}: {stat[:200]})_"
        if not stat:
            return f"_(no changes vs {ref})_"
        ok, diff = _run(["git", "diff", ref], cwd)
    else:  # fresh repo, no commits yet: status only
        stat = ""
        ok, status_only = _run(["git", "status", "--porcelain"], cwd)
        if not ok or not (status_only or "").strip():
            return "_(no commits yet, working tree clean)_"
        return f"_(no commits yet)_ status:\n```\n{status_only[:2000]}\n```"
    body = (diff or "")[:cap]
    if len(diff or "") > cap:
        body += f"\n... [diff truncated at {cap} chars]"
    ok, status = _run(["git", "status", "--porcelain"], cwd)
    untracked = [ln for ln in (status or "").splitlines() if ln.startswith("??")]
    tail = f"\n({len(untracked)} untracked files not shown)" if untracked else ""
    head = f"**diff vs {ref}**\n```\n{stat}\n```\n" if stat else ""
    return f"{head}```diff\n{body}\n```{tail}"
