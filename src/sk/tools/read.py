"""Tools submodule: read-only tools: list_dir, read_file, exec, sysinfo + allowlist. (split from sk/tools.py, pure move)."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from ..procutil import run_bounded

ALLOWED_BINARIES = {
    "ls",
    "pwd",
    "echo",
    "df",
    "du",
    "wc",
    "whoami",
    "uname",
    "cat",
    "head",
    "tail",
    "find",
    "lsblk",
    "git",
    "ollama",
    "free",
    "nproc",
    "nvidia-smi",
    "lscpu",
}

ALLOWED_GIT = {"status", "log", "branch", "diff", "remote"}

# ollama run/pull/push reach the network and write models: exec stays
# read-only, so only the local inventory subcommands are allowed.
ALLOWED_OLLAMA = {"list", "show", "ps"}


def _read_block_targets() -> list[Path]:
    """Sensitive roots reads must never touch: keys, agent history DB.

    Deliberately narrower than WRITE_BLOCKLIST: /etc stays readable
    (hostname/os-release grounding), only secret-bearing paths block.
    """
    home = Path.home()
    cands = [home / ".ssh", home / ".gnupg", home / ".sidekick" / "history.db"]
    out: list[Path] = []
    for cand in cands:
        out.append(cand)
        try:
            out.append(cand.resolve())
        except Exception:
            pass
    return out


def _check_read_path(path: str) -> Path | str:
    """Path if readable, else an Error string. Sensitive roots always refuse."""
    try:
        p = Path(path).expanduser().resolve()
    except Exception as e:
        return f"Error: bad path: {e}"
    for blocked in _read_block_targets():
        try:
            if p == blocked or blocked in p.parents:
                return f"Error: reads of {p} are blocked (sensitive path)."
        except Exception:
            pass
    return p


BLOCKED_CHARS = {";", "&", "|", ">", "<", "`", "$", "(", ")", "\n"}


def _check_cmd(cmd: str) -> tuple[str, list[str]] | str:
    """Return (binary, argv) if allowed, else error string."""
    for ch in BLOCKED_CHARS:
        if ch in cmd:
            return f"Blocked: shell metachar '{ch}' not allowed (no chaining/redirect)."
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        return f"Error: parse error: {e}"
    if not argv:
        return "Error: empty command."
    binary = argv[0]
    # allow full paths like /bin/ls
    binary_name = Path(binary).name
    if binary_name not in ALLOWED_BINARIES:
        return f"Blocked: '{binary_name}' not in allowlist {sorted(ALLOWED_BINARIES)}"
    if binary_name == "git":
        if len(argv) < 2 or argv[1] not in ALLOWED_GIT:
            return f"Blocked: only git {sorted(ALLOWED_GIT)} allowed."
    if binary_name == "find":
        # prevent find -exec / -delete
        if "-exec" in argv or "-delete" in argv:
            return "Blocked: find -exec/-delete not allowed."
    if binary_name == "ollama":
        if len(argv) < 2 or argv[1] not in ALLOWED_OLLAMA:
            return f"Blocked: only ollama {sorted(ALLOWED_OLLAMA)} allowed."
    if binary_name in ("ls", "cat", "head", "tail", "wc", "du", "find"):
        # file-dumping/listing binaries must not reach sensitive paths
        # (read_file/list_dir enforce the same guard below).
        operands = [a for a in argv[1:] if not a.startswith("-")]
        if not operands and binary_name in ("ls", "du"):
            operands = ["."]  # bare ls/du list the cwd: screen it too
        for op in operands:
            if isinstance(_check_read_path(op), str):
                return f"Blocked: reads of '{op}' are blocked (sensitive path)."
    return (binary_name, argv)


def tool_list_dir(path: str = ".") -> str:
    try:
        checked = _check_read_path(path)
        if isinstance(checked, str):
            return checked
        p = checked
        if not p.exists():
            return f"Error: {p} does not exist."
        if not p.is_dir():
            return f"Error: {p} is not a directory."
        entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        lines = [f"{'[dir] ' if e.is_dir() else '[file]':6} {e.name}" for e in entries[:100]]
        if len(entries) > 100:
            lines.append(f"... +{len(entries) - 100} more")
        return f"{p}:\n" + "\n".join(lines) if lines else f"{p}: (empty)"
    except Exception as e:
        return f"Error: {e}"


MAX_READ_BYTES = 500_000
MAX_RANGE_LINES = 2_000


def _parse_optional_int(value: object, name: str) -> tuple[int | None, str | None]:
    """Parse a JSON-ish integer argument. Empty/None means absent."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None, f"Error: {name} must be an integer."
    try:
        return int(value), None
    except Exception:
        return None, f"Error: {name} must be an integer."


def _range_body(rows: list[str], max_chars: int) -> tuple[str, int, bool]:
    """Join numbered rows, keeping whole lines under max_chars when possible.

    Returns (body, returned_line_count, truncated). If the first row alone is
    longer than the cap, it is cut; the header/footer still report truncation.
    """
    body = "\n".join(rows)
    if len(body) <= max_chars:
        return body, len(rows), False
    cut = body.rfind("\n", 0, max_chars)
    if cut < 0:
        return body[:max_chars], 1, True
    body = body[:cut]
    return body, body.count("\n") + 1, True


def tool_read_file(
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    max_chars: int = 8000,
) -> str:
    """Read a text file, optionally as an exact 1-based numbered line range.

    With no range, the historical behavior is preserved: return the file from
    the beginning, capped at max_chars chars. With start_line/end_line, return
    only those lines, each prefixed with its line number, plus a header and
    explicit truncation/clamping metadata. Binary and oversized files refuse.
    """
    try:
        checked = _check_read_path(path)
        if isinstance(checked, str):
            return checked
        p = checked
        if not p.exists():
            return f"Error: {p} does not exist."
        if p.is_dir():
            return f"Error: {p} is a directory, use list_dir."
        size = p.stat().st_size
        if size > MAX_READ_BYTES:
            return f"Error: file too large ({size} bytes), refusing."
        with p.open("rb") as fh:
            sample = fh.read(8192)
        if b"\x00" in sample:
            return f"Error: {p} looks binary (NUL byte), refusing."

        limit, err = _parse_optional_int(max_chars, "max_chars")
        if err:
            return err
        if limit is None:
            limit = 8000
        if limit < 1:
            return "Error: max_chars must be positive."
        text = p.read_text(errors="replace")

        # No range: preserve the original raw output and truncation marker.
        if start_line is None and end_line is None:
            if len(text) > limit:
                text = text[:limit] + f"\n... [truncated {len(text) - limit} chars]"
            return text

        start, err = _parse_optional_int(start_line, "start_line")
        if err:
            return err
        end, err = _parse_optional_int(end_line, "end_line")
        if err:
            return err
        if start is None:
            start = 1
        if start < 1:
            return "Error: start_line must be >= 1."
        if end is not None and end < 1:
            return "Error: end_line must be >= 1."

        lines = text.splitlines()
        total = len(lines)
        if total == 0:
            return f"Error: {p} is empty (0 lines)."
        if start > total:
            return f"Error: start_line {start} is beyond end of file ({total} lines)."

        requested_end = end
        if end is None:
            end = total
        if end < start:
            return f"Error: end_line {end} is before start_line {start}."
        clamped = end > total
        if clamped:
            end = total
        selected = end - start + 1
        line_capped = selected > MAX_RANGE_LINES
        if line_capped:
            end = start + MAX_RANGE_LINES - 1

        rows = [f"{n}: {lines[n - 1]}" for n in range(start, end + 1)]
        body, returned, char_capped = _range_body(rows, limit)
        last = start + returned - 1
        remaining = selected - returned

        meta = f"{returned} line" + ("s" if returned != 1 else "")
        if clamped:
            meta += f"; end_line {requested_end} clamped to {total}"
        if line_capped:
            meta += f"; line cap {MAX_RANGE_LINES} reached"
        if char_capped:
            meta += f"; char cap {limit} reached"

        out = f"{p}: lines {start}-{last} of {total} ({meta})\n{body}"
        if remaining:
            out += f"\n... [truncated before line {last + 1}; {remaining} lines not shown]"
        elif char_capped:
            out += f"\n... [line truncated at {limit} chars]"
        return out
    except Exception as e:
        return f"Error: {e}"


def tool_exec(cmd: str, timeout: int = 15) -> str:
    checked = _check_cmd(cmd)
    if isinstance(checked, str):
        return f"Error: {checked}"
    _, argv = checked
    try:
        res = run_bounded(argv, timeout=timeout)
        out = (res.stdout or "") + (("\n[stderr]\n" + res.stderr) if res.stderr else "")
        out = out.strip() or "(no output)"
        if len(out) > 6000:
            out = out[:6000] + "\n... [truncated]"
        if res.overflowed:
            # killed, not merely cut off: say so, the exit code will be -9
            out += "\n... [output cap reached — the process was killed]"
        elif res.timed_out:
            return f"Error: timed out after {timeout}s"
        return f"$ {cmd}\n[exit {res.returncode}]\n{out}"
    except Exception as e:
        return f"Error: {e}"


def tool_sysinfo() -> str:
    """One-shot grounded hardware + Ollama snapshot. Fast, no chaining, cross-platform."""
    import os
    import platform

    def run(argv: list[str], timeout: int = 5) -> str:
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return (r.stdout or "").strip() or f"(no output, exit {r.returncode})"
        except FileNotFoundError:
            return f"({' '.join(argv)} not installed)"
        except subprocess.TimeoutExpired:
            return "(timed out)"
        except Exception as e:
            return f"Error: {e}"

    if platform.system() == "Darwin":
        cpu_line = run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "(unknown cpu)"
        try:
            cores = str(int(run(["sysctl", "-n", "hw.ncpu"])))
        except Exception:
            cores = "?"
        try:
            mem_bytes = int(run(["sysctl", "-n", "hw.memsize"]))
            mem = f"MemTotal: {mem_bytes / (1024**3):.1f} GiB (hw.memsize)"
        except Exception:
            mem = run(["sysctl", "-n", "hw.memsize"]) or "(unknown)"
    else:
        cpu_line = "(unknown cpu)"
        for line in run(["lscpu"]).splitlines():
            if line.startswith("Model name:"):
                cpu_line = line.split(":", 1)[1].strip()
                break
        try:
            cores = run(["nproc"]).strip().split()[0] or str(os.cpu_count() or "?")
        except Exception:
            cores = str(os.cpu_count() or "?")
        mem = run(["free", "-h"])

    if platform.system() == "Darwin":
        gpu_out = run(["system_profiler", "SPDisplaysDataType", "-detailLevel", "mini"], timeout=8)
        gpu = (
            "\n".join(
                ln.strip()[:200]
                for ln in gpu_out.splitlines()
                if "Chipset Model" in ln or ("Metal" in ln and ":" in ln)
            )
            or "GPU: (none detected)"
        )
    else:
        gpu = run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"]
        )
    disk = run(["df", "-h", "/"])
    ollama_models = run(["ollama", "list"])
    # trim verbose outputs
    if len(gpu) > 500:
        gpu = gpu[:500]
    return (
        f"CPU: {cpu_line} ({cores} threads)\n"
        f"RAM:\n{mem}\n"
        f"GPU:\n{gpu}\n"
        f"DISK (/):\n{disk}\n"
        f"OLLAMA MODELS:\n{ollama_models}"
    )
