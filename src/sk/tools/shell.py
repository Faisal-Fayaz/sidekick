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
