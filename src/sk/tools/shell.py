"""Tools submodule: general shell (approval-gated) + hard-block patterns. (split from sk/tools.py, pure move)."""

from __future__ import annotations

import subprocess
import threading


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


_sessions: dict[str, subprocess.Popen] = {}
_sessions_lock = threading.RLock()


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
    """Get-or-spawn the persistent bash for a session. Raises on failure.

    Binary unbuffered pipes: all reading goes through os.read on the fd so
    no hidden layer (TextIOWrapper snapshot buffers are invisible to
    select() and would strand lines until timeout).
    """
    import os
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
            bufsize=0,
        )
        if proc.stdin is None or proc.stdout is None:
            try:
                proc.kill()
            except Exception:
                pass
            raise RuntimeError("no pipes")
        try:
            os.set_blocking(proc.stdout.fileno(), False)
        except Exception:
            pass
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
    stale = re.compile(r"^__SK_[0-9a-f]+__:\d+\s*$")
    with _sessions_lock:
        try:
            proc = _session_proc(name)
        except Exception as e:
            return f"Error: cannot start shell session: {e}"
        stdin, stdout = proc.stdin, proc.stdout
        if stdin is None or stdout is None:
            _drop_session(name)
            return "Error: shell session failed to start."
        out_fd = stdout.fileno()
        payload = (cmd + f"\nprintf '{marker}:%s\\n' \"$?\"\n").encode()
        try:
            while payload:
                written = stdin.write(payload)
                if written is None:
                    break
                payload = payload[written:]
            stdin.flush()
        except Exception as e:
            _drop_session(name)
            return f"Error: shell session died: {e}"
        out_lines: list[str] = []
        buf = bytearray()
        deadline = _t.monotonic() + timeout

        def _drain() -> list[str]:
            """Pull available bytes into lines. BlockingIOError-safe."""
            import os

            try:
                chunk = os.read(out_fd, 65536)
            except BlockingIOError:
                return []
            if chunk == b"":
                raise EOFError
            buf.extend(chunk)
            lines = []
            while True:
                i = buf.find(b"\n")
                if i < 0:
                    break
                lines.append(bytes(buf[:i]).decode("utf-8", "replace"))
                del buf[: i + 1]
            return lines

        try:
            rc = 0
            done = False
            while not done:
                for line in _drain():
                    m = want.match(line.strip())
                    if m:
                        rc = int(m.group(1))
                        done = True
                        break
                    if stale.match(line.strip()):
                        continue  # leftover marker from a timed-out call
                    out_lines.append(line)
                if done:
                    break
                remaining = deadline - _t.monotonic()
                if remaining <= 0:
                    return f"Error: timed out after {timeout}s (session kept, retry to re-sync)"
                try:
                    ready, _, _ = select.select([out_fd], [], [], remaining)
                except Exception as e:
                    _drop_session(name)
                    return f"Error: shell session failed: {e}"
                if not ready:
                    return f"Error: timed out after {timeout}s (session kept, retry to re-sync)"
        except EOFError:
            _drop_session(name)
            return "Error: shell session closed unexpectedly."
        except Exception as e:
            _drop_session(name)
            return f"Error: shell session failed: {e}"
    out = "\n".join(out_lines).strip() or "(no output)"
    if len(out) > 6000:
        out = out[:6000] + "\n... [truncated]"
    return f"$ {cmd}\n[exit {rc}]\n{out}"
