"""Tools submodule: general shell (approval-gated) + hard-block patterns. (split from sk/tools.py, pure move).

Hard blocks are deliberately checked BEFORE approval, in
``agent._gated_dispatch`` -> ``irreversible_refusal``, so that ``--yes``,
``/yolo``, ``--allow`` and ``sk mcp --allow-writes`` cannot wave through a
catastrophic command. ``tool_shell`` re-checks as a defence in depth.

Matching runs against a de-obfuscated *variant* of the command rather than the
raw string, so quoting, indirection, and nesting cannot hide a destructive
target. Known ceiling: a shell string denylist is structurally incomplete --
this is a guardrail against an honest-but-wrong agent, not a sandbox against an
adversarial one. See #298 and ROADMAP "Security threat model" (#113).
"""

from __future__ import annotations

import re
import subprocess
import threading


def _normalize_shell(cmd: str) -> str:
    """Canonicalize quoting so block patterns can't be dodged.

    Strips single/double quotes and unbraces ${HOME}: rm -rf "$HOME",
    '$HOME', ${HOME}, "~" all reduce to the bare forms the patterns match.
    Over-blocking direction only (a quoted catastrophic spelling is still
    catastrophic unquoted).
    """
    c = (cmd or "").replace("${HOME}", "$HOME")
    return c.replace("'", "").replace('"', "")


def _check_shell(cmd: str) -> str | None:
    """Hard blocks. Returns error or None if allowed (approval still applies)."""
    if not (cmd or "").strip():
        return "Error: empty command."
    if len(cmd) > 2000:
        return "Error: command too long (>2000 chars)."
    if _hard_block(cmd):
        return "Error: blocked destructive command (refused even with approval)."
    return None


def _hard_block(cmd: str) -> str | None:
    """Match every block pattern against every de-obfuscated variant of `cmd`."""
    import re

    for variant in _detection_variants(cmd):
        for pat in SHELL_BLOCK_PATTERNS:
            if re.search(pat, variant):
                return "blocked"
    return None


def irreversible_refusal(name: str, args: dict) -> str | None:
    """Pre-approval hard refusal. Runs BEFORE any authority is granted.

    This is the structural half of #298: `tool_shell` re-checks, but that runs
    after the approval callback, so `--yes` / `/yolo` / `--allow` /
    `--allow-writes` used to bypass it. Called from `agent._gated_dispatch`
    ahead of the approval step. Never raises.
    """
    try:
        if name == "shell":
            return _check_shell(str(args.get("cmd") or ""))
        if name == "shell_session":
            return _check_shell(str(args.get("cmd") or ""))
    except Exception:
        # Fail closed: an unparseable destructive command is a refusal, never a
        # silent pass.
        return "Error: blocked destructive command (refusal check failed closed)."
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
    # rm with any recursive spelling at / | /* | /.. | ~ | $HOME (short or
    # long flags, any order) plus bare system roots (/etc, /proc, /sys,
    # /dev, /usr, /boot — bare, trailing-slash, or /* glob). Legit subpaths
    # (~/x, /tmp/y, /.cache, /etc/hostname) never match: targets must be
    # bare. Non-recursive rm passes (no flag). Commands are quote-stripped
    # first (see _normalize_shell), so quoted spellings match too.
    r"\brm\b(?=[^;&|]*?(?:\s-[a-zA-Z]*[rR][a-zA-Z]*|--recursive\b))[^;&|]*?(?:(?<![\w/])/(?=[\s;$&|]|$)|(?<![\w/])/\*(?=[\s;$&|]|$)|/\.\.(?=[\s/;$&|]|$)|(?<![\w/])~(?=[\s;$&|]|$)|(?<![\w/])~/(?=[\s;$&|]|$)|\$HOME(?=[\s;$&|]|$)|\$HOME/(?=[\s;$&|]|$)|(?<![\w/])/(?:etc|proc|sys|dev|usr|boot)(?:/(?=[\s;$&|]|$)|/\*(?=[\s;$&|]|$)|(?=[\s;$&|]|$)))",
    r"\bmkfs(\s|$|\.)",  # mkfs
    r"\bdd\b.*\bof=/dev/",  # dd to devices
    # fork bomb: self-piping backgrounded self-call (any name, any spacing).
    # Ordinary functions (different/no pipe/background) never match.
    r"([A-Za-z_:][\w:]*)\(\)\s*\{\s*\1\s*\|\s*\1\s*&\s*\}",
    # redirect onto disk devices (null/zero/stdout/... stay allowed).
    r">\s*/dev/(?:sd\w+|nvme\w+|mmcblk\w+|vd\w+|hd\w+|loop\d+|dm-\d+|md\d+)(?![\w])",
    r"\bshred\b.*\/dev\/",
    # chmod <recursive> 777 / in any flag/mode order.
    r"\bchmod\b(?=[^;&|]*?(?:\s-[a-zA-Z]*[rR][a-zA-Z]*|--recursive\b))[^;&|]*?\b777\b[^;&|]*?(?<![\w/])/(?![\w/*])",
    # chown <recursive> / — same shape as chmod (ownership wipe).
    r"\bchown\b(?=[^;&|]*?(?:\s-[a-zA-Z]*[rR][a-zA-Z]*|--recursive\b))[^;&|]*?(?<![\w/])/(?![\w/*])",
]

# Shapes the nine originals above did not cover. All are matched against the
# de-obfuscated variants from _detection_variants, never the raw string alone.
SHELL_BLOCK_PATTERNS += [
    # `find / -delete` and `find / -exec rm {}` destroy trees without any rm
    # flag, so they never matched the rm pattern. (Already blocked in `exec`;
    # the shell tool had no coverage.)
    r"\bfind\b[^;&|]*?(?:/\s+-delete|\s[^;&|]*\s-delete\b)",
    r"\bfind\b[^;&|]*?-exec\s+(?:/usr/bin/|/bin/)?(?:rm|shred)\b",
    # whole-device erasure beyond dd / redirect / shred
    r"\bwipefs\b",
    r"\b(?:mkfs|mkswap|sgdisk|parted)\b[^;&|]*?/dev/(?:sd\w+|nvme\w+|mmcblk\w+|vd\w+|hd\w+|xvd\w+)(?![\w])",
    # remote code piped straight into a shell
    r"\b(?:curl|wget)\b[^;&|]*?\|\s*(?:sudo\s+)?(?:ba|z|k)?sh\b",
    # recursive delete aimed at a top-level system directory, not just bare `/`
    r"\brm\b(?=[^;&|]*?(?:\s-[a-zA-Z]*[rR][a-zA-Z]*|--recursive\b))[^;&|]*?"
    r"(?<![\w/~])/(?:etc|usr|bin|sbin|boot|lib|lib32|lib64|var|dev|proc|sys|opt|home|root|srv)"
    r"(?=[\s;|&'\"$]|$)",
    # recursive delete of the entire home tree, any flag spelling
    r"\brm\b(?=[^;&|]*?(?:\s-[a-zA-Z]*[rR][a-zA-Z]*|--recursive\b))[^;&|]*?~/?(?=[\s;|&'\"$]|$)",
]


# --- de-obfuscation for detection -------------------------------------------
# Scoped to command positions on purpose: rewriting IFS / ANSI-C / quoting inside
# *arguments* promotes data into executable syntax and yields false positives
# (e.g. `echo r\m -rf /` is not a destructive command). Same reasoning as the
# reference implementation's command-position scoping.

_SEGMENT_RE = re.compile(r"&&|\|\||[;\n]|\||&(?!&)")
_ASSIGN_RE = re.compile(r"^\s*(?:export\s+|local\s+|declare\s+|readonly\s+)?([A-Za-z_]\w*)=(.*)$", re.S)
_ANSI_C_RE = re.compile(r"\$'((?:[^'\\]|\\.)*)'")
_HOME_VARS = ("$HOME", "$USER")

_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
}


def _decode_ansi_c(text: str) -> str:
    """$'\\x72\\x6d' -> 'rm', so an encoded command name is visible to matching."""

    def sub(m: re.Match) -> str:
        body, out, i = m.group(1), [], 0
        while i < len(body):
            ch = body[i]
            if ch != "\\" or i + 1 >= len(body):
                out.append(ch)
                i += 1
                continue
            nxt = body[i + 1]
            if nxt in ("x", "X") and i + 3 < len(body):
                try:
                    out.append(chr(int(body[i + 2 : i + 4], 16)))
                    i += 4
                    continue
                except ValueError:
                    pass
            if nxt.isdigit() and i + 3 < len(body):
                try:
                    out.append(chr(int(body[i + 1 : i + 4], 8)))
                    i += 4
                    continue
                except ValueError:
                    pass
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
        return "".join(out)

    return _ANSI_C_RE.sub(sub, text)


def _collect_assignments(text: str) -> dict[str, str]:
    """Inline simple VAR=value assignments so `$VAR` command indirection resolves."""
    env: dict[str, str] = {}
    for seg in _SEGMENT_RE.split(text):
        m = _ASSIGN_RE.match(seg)
        if m:
            env[m.group(1)] = m.group(2).strip().strip("\"'")
    for _ in range(2):  # two passes resolves chained refs; a cycle just stops changing
        for name, val in env.items():
            for other, oval in env.items():
                val = val.replace("${" + other + "}", oval).replace("$" + other, oval)
            env[name] = val
    return env


def _resolve_word(word: str, env: dict[str, str]) -> str:
    """Resolve one command-position word: strip quotes, expand $VAR and $'..'."""
    w = _decode_ansi_c(word)
    for name, val in env.items():
        w = w.replace("${" + name + "}", val).replace("$" + name, val)
    w = re.sub(r"\\([^\s])", r"\1", w)  # r\m -> rm
    w = w.replace("''", "").replace('""', "")  # r''m / r""m -> rm
    return w.strip("\"'")


def _expand_home(text: str) -> str:
    for var in _HOME_VARS:
        text = text.replace("${" + var[1:] + "}", "~").replace(var, "~")
    return text


def _detection_variants(cmd: str) -> list[str]:
    """De-obfuscated forms of `cmd` to match block patterns against.

    Layers, each a superset of the last, so a hit on any variant blocks:
      1. `cmd` itself — preserves the original matching behaviour exactly.
      2. `_normalize_shell` (from #264) — quote stripping and ${HOME} unbracing.
      3. ANSI-C decoding, assignment inlining, and command-position word
         resolution, so `$'\\x72\\x6d'`, `R=rm; $R`, and `r""m` resolve. Only the
         command word of each segment is rewritten; touching arguments would
         promote data into executable syntax and false-positive on
         `echo r\\m -rf /`.
    """
    variants = [cmd, _normalize_shell(cmd)]
    try:
        decoded = _decode_ansi_c(cmd)
        env = _collect_assignments(decoded)
        segs: list[str] = []
        for seg in _SEGMENT_RE.split(decoded):
            stripped = seg.strip()
            if not stripped or _ASSIGN_RE.match(seg):
                continue
            head, sep, rest = stripped.partition(" ")
            word = _resolve_word(head, env)
            segs.append(f"{word} {rest}" if sep else word)
        if segs:
            joined = "\n".join(segs)
            variants.append(joined)
            variants.append(_normalize_shell(joined))
            variants.append(_expand_home(_normalize_shell(joined)))
    except Exception:
        return variants
    return variants


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
