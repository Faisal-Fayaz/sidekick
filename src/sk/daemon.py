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

LAUNCHD_LABEL = "com.sidekick.daemon"

# Do-not-disturb window (22:00–07:00 local): daemon nudges log only, no popup.
DND_START = 22
DND_END = 7


def unit_path() -> Path:
    """Destination of the user-level systemd unit."""
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def launchd_path() -> Path:
    """Destination of the user-level launchd plist (macOS)."""
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


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


def launchd_text(interval: int = 300, disk_warn: int = 90) -> str:
    """Render com.sidekick.daemon.plist. Pure function, safe to unit test."""
    import xml.sax.saxutils as _sax

    cmd = _sax.escape(sk_bin())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{cmd}</string>
        <string>daemon</string>
        <string>--interval</string>
        <string>{int(interval)}</string>
        <string>--disk-warn</string>
        <string>{int(disk_warn)}</string>
    </array>
    <key>StartInterval</key>
    <integer>{int(interval)}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{_sax.escape(str(Path.home() / ".sidekick" / "daemon.out.log"))}</string>
    <key>StandardErrorPath</key>
    <string>{_sax.escape(str(Path.home() / ".sidekick" / "daemon.err.log"))}</string>
</dict>
</plist>
"""


def has_launchd() -> bool:
    """True when launchd user agents are available (macOS with launchctl)."""
    import platform
    import shutil

    if platform.system() != "Darwin":
        return False
    return shutil.which("launchctl") is not None


def _launchctl(*args: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return (r.returncode == 0, out or "ok")
    except Exception as e:
        return (False, str(e))


def install_launchd(interval: int = 300, disk_warn: int = 90, path: Path | None = None) -> str:
    """Write plist + bootstrap the agent. Returns human message."""
    if not has_launchd():
        return (
            "No launchd found (need macOS + launchctl). Run `sk daemon --once` from cron instead."
        )
    dest = path or launchd_path()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(launchd_text(interval, disk_warn))
    except Exception as e:
        return f"Error writing {dest}: {e}"
    _launchctl("bootout", f"gui/{_gui_uid()}", str(dest))
    ok, msg = _launchctl("bootstrap", f"gui/{_gui_uid()}", str(dest))
    if not ok:
        return f"Installed {dest} but bootstrap failed: {msg} — load it with `launchctl bootstrap gui/$(id -u) {dest}`"
    return f"Installed + started {dest} (checks every {interval}s). Status: `launchctl print gui/$(id -u)/{LAUNCHD_LABEL}`"


def _gui_uid() -> str:
    """Current console uid for launchctl gui/ domain. Falls back to `id -u`."""
    try:
        r = subprocess.run(["id", "-u"], capture_output=True, text=True, timeout=5)
        return (r.stdout or "").strip() or "501"
    except Exception:
        return "501"


def remove_launchd(path: Path | None = None) -> str:
    """Bootout + delete the plist. Returns human message."""
    if not has_launchd():
        return "No launchd found — nothing to remove."
    dest = path or launchd_path()
    _launchctl("bootout", f"gui/{_gui_uid()}", str(dest))
    try:
        if dest.exists():
            dest.unlink()
    except Exception as e:
        return f"Error removing {dest}: {e}"
    return f"Removed {dest}."


def in_dnd(hour: int, start: int = DND_START, end: int = DND_END) -> bool:
    """True when hour (0-23 local) falls in the do-not-disturb window.

    Handles overnight wrap (start > end) and degenerate start == end (never DND).
    Pure function, safe to unit test.
    """
    try:
        h, s, e = int(hour) % 24, int(start) % 24, int(end) % 24
    except (TypeError, ValueError):
        return False
    if s == e:
        return False
    if s < e:
        return s <= h < e
    return h >= s or h < e


def notify_backend() -> str:
    """'notify-send' | 'osascript' | 'terminal-notifier' | '' (log fallback)."""
    import shutil

    for tool in ("notify-send", "osascript", "terminal-notifier"):
        if shutil.which(tool):
            return tool
    return ""


def notify(title: str, body: str, force: bool = False) -> str:
    """Desktop nudge, DND-aware. Returns 'sent:<backend>' | 'dnd' | 'logged'.

    Never raises: any failure falls back to appending nudges.log.
    Pass force=True for explicit user-invoked checks (`sk daemon --once`).
    """
    import datetime

    if not force and in_dnd(datetime.datetime.now().hour):
        append_log([f"{title}: {body}"])
        return "dnd"
    backend = notify_backend()
    try:
        if backend == "notify-send":
            r = subprocess.run(
                ["notify-send", title[:100], body[:500]],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0:
                return "sent:notify-send"
        elif backend == "osascript":
            r = subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{body[:500]}" with title "{title[:100]}"',
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0:
                return "sent:osascript"
        elif backend == "terminal-notifier":
            r = subprocess.run(
                ["terminal-notifier", "-title", title[:100], "-message", body[:500]],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0:
                return "sent:terminal-notifier"
    except Exception:
        pass
    append_log([f"{title}: {body}"])
    return "logged"


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
