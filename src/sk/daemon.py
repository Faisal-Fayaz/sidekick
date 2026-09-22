"""Daemon watcher: cheap periodic checks. No LLM. State in ~/.sidekick/daemon.json."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

STATE_PATH = Path.home() / ".sidekick" / "daemon.json"
NUDGES_LOG = Path.home() / ".sidekick" / "nudges.log"

UNIT_NAME = "sidekick-daemon.service"


def unit_path() -> Path:
    """Destination of the user-level systemd unit."""
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def sk_bin() -> str:
    """How the unit should invoke sidekick: `sk` on PATH, else `python -m sk`."""
    import shutil
    import sys

    found = shutil.which("sk")
    if found:
        return found
    return f"{sys.executable} -m sk"


def unit_text(interval: int = 300, disk_warn: int = 90) -> str:
    """Render sidekick-daemon.service. Pure function, safe to unit test."""
    return f"""[Unit]
Description=Sidekick background watcher (disk, shell failures, dirty repos)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={sk_bin()} daemon --interval {int(interval)} --disk-warn {int(disk_warn)}
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
"""


def has_systemd() -> bool:
    """True when user-level systemd is available (Linux with systemd)."""
    import shutil

    if shutil.which("systemctl") is None:
        return False
    return Path("/run/systemd/system").exists()


def _systemctl(*args: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["systemctl", "--user", *args], capture_output=True, text=True, timeout=30
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return (r.returncode == 0, out or "ok")
    except Exception as e:
        return (False, str(e))


def install_unit(interval: int = 300, disk_warn: int = 90, path: Path | None = None) -> str:
    """Write unit + daemon-reload + enable --now. Returns human message."""
    if not has_systemd():
        return "No user systemd found (need Linux + systemctl). On macOS, run `sk daemon --once` from cron instead."
    dest = path or unit_path()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(unit_text(interval, disk_warn))
    except Exception as e:
        return f"Error writing {dest}: {e}"
    ok, msg = _systemctl("daemon-reload")
    if not ok:
        return f"Installed {dest} but daemon-reload failed: {msg}"
    ok, msg = _systemctl("enable", "--now", UNIT_NAME)
    if not ok:
        return f"Installed {dest} but enable failed: {msg} — start it with `systemctl --user start {UNIT_NAME}`"
    return f"Installed + started {dest} (checks every {interval}s). Status: `systemctl --user status {UNIT_NAME}`"


def remove_unit(path: Path | None = None) -> str:
    """Stop + disable + delete the unit. Returns human message."""
    if not has_systemd():
        return "No user systemd found — nothing to remove."
    dest = path or unit_path()
    _systemctl("disable", "--now", UNIT_NAME)
    try:
        if dest.exists():
            dest.unlink()
    except Exception as e:
        return f"Error removing {dest}: {e}"
    _systemctl("daemon-reload")
    return f"Removed {dest}."


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


def check_once(
    state: dict | None = None, projects: list[str] | None = None, disk_warn: int = 90
) -> tuple[list[str], dict]:
    from .brief import DEFAULT_PROJECTS

    state = dict(state or load_state())
    projs = projects if projects is not None else DEFAULT_PROJECTS
    nudges: list[str] = []

    pct = disk_use_pct()
    if pct is not None and pct >= disk_warn:
        nudges.append(
            f"disk {pct}% full (>= {disk_warn}%) — clean ~/Downloads, npm cache, old ollama models"
        )

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
