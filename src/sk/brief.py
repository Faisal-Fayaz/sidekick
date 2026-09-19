"""Morning digest: deterministic, no LLM. Fast."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

DEFAULT_PROJECTS = ["~/sidekick", "~/neural-hangar", "~/ecomind"]


def _run(argv: list[str], timeout: int = 8) -> str:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip()
    except Exception as e:
        return f"(error: {e})"


def git_snapshot(path: str) -> dict:
    p = str(Path(path).expanduser())
    if not Path(p).exists():
        return {"path": path, "exists": False}
    branch = _run(["git", "-C", p, "branch", "--show-current"])
    status = _run(["git", "-C", p, "status", "--porcelain"])
    log = _run(["git", "-C", p, "log", "--oneline", "-3"])
    n_changed = len([l for l in status.splitlines() if l.strip()])
    return {"path": path, "exists": True, "branch": branch or "(detached)", "changed": n_changed, "status": status[:800], "log": log[:800]}


def gather_brief(projects: list[str] | None = None) -> dict:
    from .store import list_memories, list_todos
    from .tools import tool_sysinfo

    projs = projects or DEFAULT_PROJECTS
    try:
        memories = list_memories(limit=5)
    except Exception:
        memories = []
    try:
        todos = list_todos(open_only=True)[:5]
    except Exception:
        todos = []
    try:
        sysinfo = tool_sysinfo()
    except Exception as e:
        sysinfo = f"(sysinfo failed: {e})"
    snaps = [git_snapshot(p) for p in projs]
    return {"when": datetime.now().strftime("%a %Y-%m-%d %H:%M"), "sysinfo": sysinfo, "projects": snaps, "memories": memories, "todos": todos}
