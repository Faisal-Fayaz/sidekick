"""Tools submodule: read-only tools: list_dir, read_file, exec, sysinfo + allowlist. (split from sk/tools.py, pure move)."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

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
    "python3",
    "git",
    "ollama",
    "free",
    "nproc",
    "nvidia-smi",
    "lscpu",
}

ALLOWED_GIT = {"status", "log", "branch", "diff", "remote"}

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
    return (binary_name, argv)


def tool_list_dir(path: str = ".") -> str:
    try:
        p = Path(path).expanduser().resolve()
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


def tool_read_file(path: str, max_chars: int = 8000) -> str:
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: {p} does not exist."
        if p.is_dir():
            return f"Error: {p} is a directory, use list_dir."
        if p.stat().st_size > 500_000:
            return f"Error: file too large ({p.stat().st_size} bytes), refusing."
        text = p.read_text(errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... [truncated {len(text) - max_chars} chars]"
        return text
    except Exception as e:
        return f"Error: {e}"


def tool_exec(cmd: str, timeout: int = 15) -> str:
    checked = _check_cmd(cmd)
    if isinstance(checked, str):
        return f"Error: {checked}"
    _, argv = checked
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        out = (res.stdout or "") + (("\n[stderr]\n" + res.stderr) if res.stderr else "")
        out = out.strip() or "(no output)"
        if len(out) > 6000:
            out = out[:6000] + "\n... [truncated]"
        return f"$ {cmd}\n[exit {res.returncode}]\n{out}"
    except subprocess.TimeoutExpired:
        return f"Error: timed out after {timeout}s"
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
