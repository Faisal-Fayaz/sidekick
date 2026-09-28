"""Tools submodule: general shell (approval-gated) + hard-block patterns. (split from sk/tools.py, pure move)."""

from __future__ import annotations

import subprocess


def _check_shell(cmd: str) -> str | None:
    """Hard blocks. Returns error or None if allowed (approval still applies)."""
    import re

    if not (cmd or "").strip():
        return "Error: empty command."
    if len(cmd) > 2000:
        return "Error: command too long (>2000 chars)."
    for pat in SHELL_BLOCK_PATTERNS:
        if re.search(pat, cmd):
            return "Error: blocked destructive command (refused even with approval)."
    return None


def tool_shell(cmd: str, timeout: int = 30) -> str:
    """General shell via bash -c. Approval-gated; catastrophic patterns hard-blocked."""
    blocked = _check_shell(cmd)
    if blocked:
        return blocked
    timeout = max(5, min(int(timeout or 30), 120))
    try:
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)
        out = (res.stdout or "") + (("\n[stderr]\n" + res.stderr) if res.stderr else "")
        out = out.strip() or "(no output)"
        if len(out) > 6000:
            out = out[:6000] + "\n... [truncated]"
        return f"$ {cmd}\n[exit {res.returncode}]\n{out}"
    except subprocess.TimeoutExpired:
        return f"Error: timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


SHELL_BLOCK_PATTERNS = [
    r"\brm\s+(-[a-z]*r[a-z]*\s+)+/(?:\s|$)",  # rm -rf /
    r"\brm\s+(-[a-z]*r[a-z]*\s+)+/\*",  # rm -rf /*
    r"\brm\s+(-[a-z]*r[a-z]*\s+)+(~|\$HOME)(?:\s|$)",  # rm -rf ~ / $HOME
    r"\bmkfs(\s|$|\.)",  # mkfs
    r"\bdd\b.*\bof=/dev/",  # dd to devices
    r":\(\)\s*\{",  # fork bomb
    r">\s*/dev/sd[a-z]",  # redirect onto disks
    r"\bshred\b.*\/dev\/",
    r"\bchmod\s+-R\s+777\s+/",  # chmod -R 777 /
]


_sessions: dict[str, "subprocess.Popen"] = {}
_sessions_lock = __import__("threading").RLock()


def _drop_session(name: str) -> None:
    """Terminate + forget a session process. Never raises."""
    try:
        with _sessions_lock:
            proc = _sessions.pop(name, None)
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        proc.kill()
            except Exception:
                pass
            for stream in (proc.stdin, proc.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
    except Exception:
        pass


def _close_all_sessions() -> None:
    try:
        for name in list(_sessions):
            _drop_session(name)
    except Exception:
        pass


try:
    import atexit as _atexit

    _atexit.register(_close_all_sessions)
except Exception:
    pass


def _session_proc(name: str):
    """Get-or-spawn the persistent bash for a session. Raises on failure."""
    import subprocess

    with _sessions_lock:
        proc = _sessions.get(name)
        if proc is not None and proc.poll() is None:
            return proc
        if proc is not None:
            _sessions.pop(name, None)
        proc = subprocess.Popen(
            ["bash", "--noprofile", "--norc"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        _sessions[name] = proc
        return proc


def tool_shell_session(cmd: str, session: str = "default", timeout: int = 30) -> str:
    """Run a command in a persistent bash session (cwd/env survive calls).

    Approval-gated + hard-blocked like shell. `exit` closes the session.
    Timed-out commands keep running detached; the next call re-syncs on its
    own marker, so state is preserved. Never raises (returns Error strings).
    """
    import re
    import select
    import time as _t
    import uuid

    blocked = _check_shell(cmd)
    if blocked:
        return blocked
    name = (session or "default").strip()[:64] or "default"
    if cmd.strip() == "exit":
        _drop_session(name)
        return f"Closed shell session '{name}'."
    timeout = max(5, min(int(timeout or 30), 120))
    marker = f"__SK_{uuid.uuid4().hex}__"
    want = re.compile(rf"^{re.escape(marker)}:(\d+)\s*$")
    with _sessions_lock:
        try:
            proc = _session_proc(name)
        except Exception as e:
            return f"Error: cannot start shell session: {e}"
        try:
            proc.stdin.write(cmd + f"\nprintf '{marker}:%s\\n' \"$?\"\n")
            proc.stdin.flush()
        except Exception as e:
            _drop_session(name)
            return f"Error: shell session died: {e}"
        out_lines = []
        deadline = _t.monotonic() + timeout

        def _line_ready() -> bool:
            # select() cannot see lines already sitting in Python's read
            # buffer (coalesced writes); check the buffer first or a ready
            # marker would hang until timeout.
            try:
                return b"\n" in proc.stdout.buffer.peek()
            except Exception:
                return False

        try:
            while True:
                if not _line_ready():
                    remaining = deadline - _t.monotonic()
                    if remaining <= 0:
                        return f"Error: timed out after {timeout}s (session kept, retry to re-sync)"
                    ready, _, _ = select.select([proc.stdout], [], [], remaining)
                    if not ready:
                        return f"Error: timed out after {timeout}s (session kept, retry to re-sync)"
                line = proc.stdout.readline()
                if line == "":
                    _drop_session(name)
                    return "Error: shell session closed unexpectedly."
                m = want.match(line.strip())
                if m:
                    rc = int(m.group(1))
                    break
                out_lines.append(line.rstrip("\n"))
        except Exception as e:
            _drop_session(name)
            return f"Error: shell session failed: {e}"
    out = "\n".join(out_lines).strip() or "(no output)"
    if len(out) > 6000:
        out = out[:6000] + "\n... [truncated]"
    return f"$ {cmd}\n[exit {rc}]\n{out}"
