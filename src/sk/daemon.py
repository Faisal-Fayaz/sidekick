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
TIMER_NAME = "sidekick-daemon.timer"

LAUNCHD_LABEL = "com.sidekick.daemon"

# Do-not-disturb window (22:00–07:00 local): daemon nudges log only, no popup.
DND_START = 22
DND_END = 7

# Named daily slots for natural-language schedules.
NAMED_SLOTS = {"morning": (8, 0), "evening": (18, 0), "noon": (12, 0), "midnight": (0, 0)}


def parse_schedule(text: str) -> dict | None:
    """Natural language → schedule spec. Pure function, never raises.

    'every morning' → daily 08:00 · 'every evening' → daily 18:00
    'every day at 9' / 'daily at 9:30' / '9am' / 'at 17:45' → daily
    'hourly' / 'every hour' → hourly at :00
    'every 30 minutes' / 'every 2 hours' → interval seconds
    None when unparseable (caller prints supported forms).
    """
    try:
        t = (text or "").strip().lower()
    except Exception:
        return None
    if not t:
        return None
    for name, (slot_h, slot_m) in NAMED_SLOTS.items():
        if re.search(rf"\b{name}\b", t):
            return {"kind": "daily", "hour": slot_h, "minute": slot_m, "text": text.strip()}
    if re.search(r"\bhourly\b|\bevery hour\b", t):
        return {"kind": "hourly", "minute": 0, "text": text.strip()}
    every = re.search(r"every\s+(\d+)\s*(minute|minutes|hour|hours)\b", t)
    if every:
        n = max(1, int(every.group(1)))
        seconds = n * 60 if every.group(2).startswith("minute") else n * 3600
        return {"kind": "interval", "seconds": seconds, "text": text.strip()}
    clock = re.search(r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", t)
    if clock and ("at" in t or "daily" in t or "every" in t or clock.group(3)):
        h, mi, ap = int(clock.group(1)), int(clock.group(2) or 0), clock.group(3)
        if h > 23 or mi > 59:
            return None
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        return {"kind": "daily", "hour": h, "minute": mi, "text": text.strip()}
    return None


def describe_schedule(spec: dict | None) -> str:
    """Human one-liner for a spec. '(none)' when unset."""
    s = spec or {}
    try:
        kind = s.get("kind", "")
        if kind == "daily":
            return f"daily at {int(s.get('hour', 8)):02d}:{int(s.get('minute', 0)):02d}"
        if kind == "hourly":
            return "hourly at :00"
        if kind == "interval":
            return f"every {int(s.get('seconds', 300))}s"
    except Exception:
        pass
    return "(none)"


def set_schedule(text: str) -> tuple[dict | None, str]:
    """Parse + persist a schedule in daemon.json. Returns (spec, message)."""
    spec = parse_schedule(text)
    if spec is None:
        return (
            None,
            "Could not parse schedule. Try 'every morning', 'daily at 9:30', "
            "'hourly', or 'every 30 minutes'.",
        )
    try:
        state = load_state()
        state["schedule"] = spec
        save_state(state)
    except Exception as e:
        return (None, f"Error saving schedule: {e}")
    return (spec, f"Scheduled: {describe_schedule(spec)}.")


def get_schedule() -> dict | None:
    """Current persisted schedule, or None. Never raises."""
    try:
        spec = load_state().get("schedule")
        return spec if isinstance(spec, dict) else None
    except Exception:
        return None


def clear_schedule() -> str:
    """Remove the persisted schedule. Returns human message."""
    try:
        state = load_state()
        state.pop("schedule", None)
        save_state(state)
    except Exception as e:
        return f"Error clearing schedule: {e}"
    return "Schedule cleared (watcher keeps its install, interval mode)."


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


def unit_text(interval: int = 300, disk_warn: int = 90, schedule: dict | None = None) -> str:
    """Render sidekick-daemon.service. Pure function, safe to unit test.

    With a daily/hourly schedule the service runs `daemon --once` (the
    companion timer_text() fires it); interval mode keeps the loop.
    """
    if (schedule or {}).get("kind") in ("daily", "hourly"):
        exec_line = f"ExecStart={sk_bin()} daemon --once --disk-warn {int(disk_warn)}"
    else:
        exec_line = (
            f"ExecStart={sk_bin()} daemon --interval {int(interval)} --disk-warn {int(disk_warn)}"
        )
    return f"""[Unit]
Description=Sidekick background watcher (disk, shell failures, dirty repos)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
{exec_line}
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
"""


def timer_path() -> Path:
    """Destination of the user-level systemd timer (calendar schedules)."""
    return Path.home() / ".config" / "systemd" / "user" / TIMER_NAME


def timer_text(schedule: dict) -> str:
    """Render sidekick-daemon.timer for a daily/hourly spec. Pure function."""
    spec = schedule or {}
    kind = spec.get("kind", "")
    if kind == "daily":
        on_cal = f"*-*-* {int(spec.get('hour', 8)):02d}:{int(spec.get('minute', 0)):02d}:00"
    else:
        on_cal = "hourly"
    return f"""[Unit]
Description=Sidekick watcher schedule ({describe_schedule(schedule)})

[Timer]
OnCalendar={on_cal}
Persistent=true

[Install]
WantedBy=timers.target
"""


def has_systemd() -> bool:
    """True when user-level systemd is available (Linux with systemd)."""
    import shutil

    if shutil.which("systemctl") is None:
        return False
    return Path("/run/systemd/system").exists()


def launchd_text(interval: int = 300, disk_warn: int = 90, schedule: dict | None = None) -> str:
    """Render com.sidekick.daemon.plist. Pure function, safe to unit test.

    Daily/hourly schedules emit StartCalendarInterval and run `daemon
    --once`; interval mode keeps StartInterval with the loop.
    """
    import xml.sax.saxutils as _sax

    cmd = _sax.escape(sk_bin())
    spec = schedule or {}
    kind = spec.get("kind", "")
    if kind == "daily":
        trigger = (
            "    <key>StartCalendarInterval</key>\n"
            "    <dict>\n"
            f"        <key>Hour</key>\n        <integer>{int(spec.get('hour', 8))}</integer>\n"
            f"        <key>Minute</key>\n        <integer>{int(spec.get('minute', 0))}</integer>\n"
            "    </dict>"
        )
        args = (
            "        <string>daemon</string>\n"
            "        <string>--once</string>\n"
            "        <string>--disk-warn</string>\n"
            f"        <string>{int(disk_warn)}</string>"
        )
    elif kind == "hourly":
        trigger = (
            "    <key>StartCalendarInterval</key>\n"
            "    <dict>\n"
            "        <key>Minute</key>\n"
            "        <integer>0</integer>\n"
            "    </dict>"
        )
        args = (
            "        <string>daemon</string>\n"
            "        <string>--once</string>\n"
            "        <string>--disk-warn</string>\n"
            f"        <string>{int(disk_warn)}</string>"
        )
    else:
        trigger = f"    <key>StartInterval</key>\n    <integer>{int(interval)}</integer>"
        args = (
            "        <string>daemon</string>\n"
            "        <string>--interval</string>\n"
            f"        <string>{int(interval)}</string>\n"
            "        <string>--disk-warn</string>\n"
            f"        <string>{int(disk_warn)}</string>"
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{cmd}</string>
{args}
    </array>
{trigger}
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


def install_launchd(
    interval: int = 300,
    disk_warn: int = 90,
    path: Path | None = None,
    schedule: str = "",
) -> str:
    """Write plist + bootstrap the agent. Returns human message.

    schedule is natural language ('every morning'); daily/hourly specs
    run `daemon --once` on a calendar trigger, interval keeps the loop.
    """
    if not has_launchd():
        return (
            "No launchd found (need macOS + launchctl). Run `sk daemon --once` from cron instead."
        )
    spec: dict | None = None
    if schedule.strip():
        spec, msg = set_schedule(schedule)
        if spec is None:
            return msg
    dest = path or launchd_path()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(launchd_text(interval, disk_warn, spec))
    except Exception as e:
        return f"Error writing {dest}: {e}"
    _launchctl("bootout", f"gui/{_gui_uid()}", str(dest))
    ok, msg = _launchctl("bootstrap", f"gui/{_gui_uid()}", str(dest))
    if not ok:
        return f"Installed {dest} but bootstrap failed: {msg} — load it with `launchctl bootstrap gui/$(id -u) {dest}`"
    mode = describe_schedule(spec) if spec else f"checks every {interval}s"
    return f"Installed + started {dest} ({mode}). Status: `launchctl print gui/$(id -u)/{LAUNCHD_LABEL}`"


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


def install_unit(
    interval: int = 300,
    disk_warn: int = 90,
    path: Path | None = None,
    schedule: str = "",
    timer: Path | None = None,
) -> str:
    """Write unit (+ timer for calendar schedules) + enable. Returns message.

    schedule is natural language ('every morning'); daily/hourly specs
    write a .timer firing `daemon --once`, interval keeps the loop service.
    """
    if not has_systemd():
        return "No user systemd found (need Linux + systemctl). On macOS, run `sk daemon --once` from cron instead."
    spec: dict | None = None
    if schedule.strip():
        spec, msg = set_schedule(schedule)
        if spec is None:
            return msg
    dest = path or unit_path()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(unit_text(interval, disk_warn, spec))
    except Exception as e:
        return f"Error writing {dest}: {e}"
    target, what = UNIT_NAME, f"checks every {interval}s"
    if (spec or {}).get("kind") in ("daily", "hourly"):
        tdest = timer or timer_path()
        try:
            tdest.parent.mkdir(parents=True, exist_ok=True)
            tdest.write_text(timer_text(spec or {}))
        except Exception as e:
            return f"Error writing {tdest}: {e}"
        target, what = TIMER_NAME, describe_schedule(spec)
    ok, msg = _systemctl("daemon-reload")
    if not ok:
        return f"Installed {dest} but daemon-reload failed: {msg}"
    ok, msg = _systemctl("enable", "--now", target)
    if not ok:
        return f"Installed {dest} but enable failed: {msg} — start it with `systemctl --user start {target}`"
    return f"Installed + started {dest} ({what}). Status: `systemctl --user status {target}`"


def remove_unit(path: Path | None = None, timer: Path | None = None) -> str:
    """Stop + disable + delete the unit (and timer, if present). Returns message."""
    if not has_systemd():
        return "No user systemd found — nothing to remove."
    dest = path or unit_path()
    _systemctl("disable", "--now", UNIT_NAME)
    _systemctl("disable", "--now", TIMER_NAME)
    removed = []
    for p in (dest, timer or timer_path()):
        try:
            if p.exists():
                p.unlink()
                removed.append(p.name)
        except Exception as e:
            return f"Error removing {p}: {e}"
    _systemctl("daemon-reload")
    return f"Removed {dest}." if not removed else f"Removed {', '.join(removed)}."


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


def digest_text(
    state: dict | None = None, projects: list[str] | None = None, disk_warn: int = 90
) -> tuple[str, dict]:
    """Morning digest: brief + overnight failures. Deterministic, no LLM.

    Composes format_brief_text(gather_brief()) with shell failures since
    the last check. Returns (text, state) with last_shell_id advanced so
    repeat digests don't re-report. Never raises (degrades to partial text).
    """
    from .brief import format_brief_text, gather_brief

    state = dict(state if state is not None else load_state())
    try:
        text = format_brief_text(gather_brief(projects))
    except Exception as e:
        text = f"**brief** (unavailable: {e})"
    try:
        fails, max_id = new_failures(int(state.get("last_shell_id", 0) or 0))
    except Exception:
        fails, max_id = [], int(state.get("last_shell_id", 0) or 0)
    if fails:
        lines = ["", "**overnight failures**"]
        for fid, cmd, cwd, rc in fails[:10]:
            lines.append(f"- #{fid} (exit {rc}): {str(cmd)[:100]} @ {cwd} — try `sk oops`")
        text += "\n" + "\n".join(lines)
    state["last_shell_id"] = max_id
    state["last_run"] = time.time()
    return (text, state)


def deliver_digest(projects: list[str] | None = None, force: bool = False) -> tuple[str, str]:
    """Build the digest once, persist state, and notify (DND-aware).

    Returns (outcome, text). notify() handles DND + logged fallback, so
    delivery never loses the digest and never raises. force=True bypasses
    DND for explicit `sk digest`.
    """
    try:
        text, state = digest_text(load_state(), projects)
        save_state(state)
    except Exception as e:
        return (f"Error building digest: {e}", "")
    head = " ".join(text.split())[:200]
    outcome = notify("sidekick morning digest", head or "(empty digest)", force=force)
    if outcome == "dnd":
        return ("Digest ready (quiet hours — logged, popup suppressed).", text)
    if outcome.startswith("sent:"):
        return (f"Digest delivered via {outcome[5:]}.", text)
    return ("Digest ready (no notifier — logged to nudges.log).", text)
