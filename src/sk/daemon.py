"""Daemon watcher: cheap periodic checks. No LLM. State in ~/.sidekick/daemon.json."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

STATE_PATH = Path.home() / ".sidekick" / "daemon.json"
NUDGES_LOG = Path.home() / ".sidekick" / "nudges.log"


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"last_shell_id": 0, "last_run": 0}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def disk_use_pct() -> int | None:
    try:
        r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=8)
        m = re.search(r"(\d+)% /", r.stdout or "")
        return int(m.group(1)) if m else None
    except Exception:
        return None


def new_failures(since_id: int, limit: int = 20) -> tuple[list[tuple], int]:
    """Shell failures with id > since_id. Returns (rows, max_id_seen)."""
    from .store import _connect

    conn = _connect()
    try:
        cur = conn.execute("SELECT MAX(id) FROM shell_history")
        max_id = cur.fetchone()[0] or 0
        cur = conn.execute(
            "SELECT id, cmd, cwd, exit FROM shell_history WHERE id > ? AND exit != 0 ORDER BY id DESC LIMIT ?",
            (since_id, limit),
        )
        return ([(r[0], r[1], r[2], r[3]) for r in cur.fetchall()], max_id or since_id)
    finally:
        conn.close()


def dirty_repos(projects: list[str]) -> list[str]:
    from .brief import git_snapshot

    dirty: list[str] = []
    for p in projects:
        try:
            s = git_snapshot(p)
            if s.get("exists") and int(s.get("changed", 0) or 0) > 0:
                dirty.append(f"{p} ({s['changed']} uncommitted)")
        except Exception:
            pass
    return dirty


def check_once(state: dict | None = None, projects: list[str] | None = None, disk_warn: int = 90) -> tuple[list[str], dict]:
    from .brief import DEFAULT_PROJECTS

    state = dict(state or load_state())
    projs = projects if projects is not None else DEFAULT_PROJECTS
    nudges: list[str] = []

    pct = disk_use_pct()
    if pct is not None and pct >= disk_warn:
        nudges.append(f"disk {pct}% full (>= {disk_warn}%) — clean ~/Downloads, npm cache, old ollama models")

    fails, max_id = new_failures(int(state.get("last_shell_id", 0) or 0))
    for fid, cmd, cwd, rc in fails:
        nudges.append(f"shell failure #{fid} (exit {rc}): {cmd[:100]} @ {cwd} — try `sk oops`")
    state["last_shell_id"] = max_id

    for d in dirty_repos(projs):
        nudges.append(f"dirty repo: {d}")

    state["last_run"] = time.time()
    return (nudges, state)


def append_log(nudges: list[str]) -> None:
    if not nudges:
        return
    NUDGES_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M")
    with open(NUDGES_LOG, "a") as f:
        for n in nudges:
            f.write(f"[{ts}] {n}\n")
