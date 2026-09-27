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
        return f"Error: {e}"


def git_snapshot(path: str) -> dict:
    p = str(Path(path).expanduser())
    if not Path(p).exists():
        return {"path": path, "exists": False}
    branch = _run(["git", "-C", p, "branch", "--show-current"])
    status = _run(["git", "-C", p, "status", "--porcelain"])
    log = _run(["git", "-C", p, "log", "--oneline", "-3"])
    n_changed = len([ln for ln in status.splitlines() if ln.strip()])
    return {
        "path": path,
        "exists": True,
        "branch": branch or "(detached)",
        "changed": n_changed,
        "status": status[:800],
        "log": log[:800],
    }


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
    return {
        "when": datetime.now().strftime("%a %Y-%m-%d %H:%M"),
        "sysinfo": sysinfo,
        "projects": snaps,
        "memories": memories,
        "todos": todos,
    }


def format_brief_text(data: dict) -> str:
    """Plain-markdown digest for /brief and other non-Rich surfaces."""
    import re

    sysinfo: str = data.get("sysinfo", "")
    keep: list[str] = []
    for line in sysinfo.splitlines():
        ll = line.lower()
        if (
            line.startswith("CPU:")
            or line.startswith("GPU:")
            or "mem:" in ll
            or "/dev/nvme" in line
            or "OLLAMA MODELS" in line
            or "qwen" in line
            or "llama" in line
            or "NAME " in line
        ):
            keep.append(line.strip())
    out = [f"**brief** {data.get('when', '')}", "", "**system**"] + [
        f"- {ln}"[:120] for ln in keep[:10]
    ]
    out += ["", "**projects**"]
    for s in data.get("projects", []):
        if not s.get("exists"):
            out.append(f"- {s['path']}: missing")
        else:
            log1 = (s.get("log") or "-").splitlines()
            out.append(
                f"- {s['path']} [{s.get('branch', '?')}] {s.get('changed', 0)} changed — {(log1[0][:60] if log1 else '-')}"
            )
    m = re.search(r"(\d+)% /", sysinfo)
    if m and int(m.group(1)) >= 90:
        out += ["", f"⚠ disk {m.group(1)}% full — prune models with `sk models prune <id>`"]
    for s in data.get("projects", []):
        if s.get("exists") and int(s.get("changed", 0) or 0) > 0:
            out.append(f"⚠ {s['path']}: {s['changed']} uncommitted")
    if data.get("todos"):
        out += ["", "**open todos**"] + [f"- ○ #{i} {t}" for i, t, _ in data["todos"][:5]]
    if data.get("memories"):
        out += ["", "**memories**"] + [f"- {x}" for x in data["memories"][:5]]
    return "\n".join(out)
