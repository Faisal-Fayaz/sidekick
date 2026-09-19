"""MVP tools: sysinfo, list_dir, read_file, exec (read-only) + write_file/edit_file (approval)."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

# --- safety policy: read-only single commands, no chaining/redirection ---

ALLOWED_BINARIES = {
    "ls", "pwd", "echo", "df", "du", "wc", "whoami", "uname",
    "cat", "head", "tail", "find", "lsblk", "python3", "git", "ollama",
    "free", "nproc", "nvidia-smi", "lscpu",
}

# git subcommands allowed (read-only)
ALLOWED_GIT = {"status", "log", "branch", "diff", "remote"}

BLOCKED_CHARS = {";", "&", "|", ">", "<", "`", "$", "(", ")", "\n"}


def _check_cmd(cmd: str) -> tuple[str, list[str]] | str:
    """Return (binary, argv) if allowed, else error string."""
    for ch in BLOCKED_CHARS:
        if ch in cmd:
            return f"Blocked: shell metachar '{ch}' not allowed in MVP (no chaining/redirect)."
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        return f"Parse error: {e}"
    if not argv:
        return "Empty command."
    binary = argv[0]
    # allow full paths like /bin/ls
    binary_name = Path(binary).name
    if binary_name not in ALLOWED_BINARIES:
        return f"Blocked: '{binary_name}' not in allowlist {sorted(ALLOWED_BINARIES)}"
    if binary_name == "git":
        if len(argv) < 2 or argv[1] not in ALLOWED_GIT:
            return f"Blocked: only git {sorted(ALLOWED_GIT)} allowed in MVP."
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
            return f"Error: file too large ({p.stat().st_size} bytes), refusing in MVP."
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
    """One-shot grounded hardware + Ollama snapshot. Fast, no chaining."""
    import shutil

    def run(argv: list[str], timeout: int = 5) -> str:
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return (r.stdout or "").strip() or f"(no output, exit {r.returncode})"
        except FileNotFoundError:
            return f"({' '.join(argv)} not installed)"
        except subprocess.TimeoutExpired:
            return "(timed out)"
        except Exception as e:
            return f"(error: {e})"

    mem = run(["free", "-h"])
    # concise CPU line
    cpu = run(["lscpu"])
    cpu_line = "(unknown cpu)"
    for line in cpu.splitlines():
        if line.startswith("Model name:"):
            cpu_line = line.split(":", 1)[1].strip()
            break
    try:
        cores = run(["nproc"]).strip().split()[0]
    except Exception:
        cores = "?"
    gpu = run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"])
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


WRITE_TOOLS = {"write_file", "edit_file"}

# never allow writes here, even with --yes
WRITE_BLOCKLIST = (
    Path.home() / ".ssh",
    Path.home() / ".gnupg",
    Path.home() / ".sidekick" / "history.db",
    Path("/etc"),
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
    Path("/boot"),
    Path("/proc"),
    Path("/sys"),
    Path("/dev"),
)


def _check_write_path(path: str) -> Path | str:
    try:
        p = Path(path).expanduser().resolve()
    except Exception as e:
        return f"Error: bad path: {e}"
    for blocked in WRITE_BLOCKLIST:
        try:
            if p == blocked or blocked in p.parents or str(p).startswith(str(blocked)):
                # careful: /usr contains /usr/lib etc; also block exact
                return f"Error: writes to {p} are blocked (sensitive path {blocked})."
        except Exception:
            pass
    # only allow writes under HOME or /tmp for MVP
    home = Path.home().resolve()
    try:
        is_home = p == home or home in p.parents
    except Exception:
        is_home = False
    is_tmp = str(p).startswith("/tmp/")
    if not (is_home or is_tmp):
        return f"Error: MVP only allows writes under {home} or /tmp (got {p})."
    return p


def tool_write_file(path: str, content: str) -> str:
    checked = _check_write_path(path)
    if isinstance(checked, str):
        return checked
    p: Path = checked
    if len(content) > 100_000:
        return "Error: content too large (>100KB), refusing in MVP."
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Wrote {len(content)} chars to {p}"
    except Exception as e:
        return f"Error: {e}"


def tool_edit_file(path: str, old_string: str, new_string: str) -> str:
    if not old_string:
        return "Error: old_string empty."
    if old_string == new_string:
        return "Error: old_string == new_string, nothing to do."
    checked = _check_write_path(path)
    if isinstance(checked, str):
        return checked
    p: Path = checked
    try:
        if not p.exists():
            return f"Error: {p} does not exist."
        if p.stat().st_size > 500_000:
            return "Error: file too large to edit in MVP."
        text = p.read_text(errors="replace")
        count = text.count(old_string)
        if count == 0:
            return "Error: old_string not found in file."
        if count > 1:
            return f"Error: old_string found {count}x, must be unique. Provide more context."
        text = text.replace(old_string, new_string, 1)
        if len(text) > 500_000:
            return "Error: result too large, refusing."
        p.write_text(text)
        return f"Edited {p} (1 replacement, {len(new_string)} chars in)"
    except Exception as e:
        return f"Error: {e}"


# OpenAI-compatible tool schemas for Ollama
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "sysinfo",
            "description": "Get REAL hardware + Ollama models (CPU, RAM, GPU, disk, installed models). MUST call this first for any 'my device / my machine / what LLM can I run' question. Never guess specs.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files in a directory. Use for exploring the filesystem.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory path, default '.'"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a small text file. Use for inspecting config/code.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "File path to read"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exec",
            "description": "Run a READ-ONLY shell command (ls, df, du, git status/log, pwd, etc). No pipes/redirects.",
            "parameters": {
                "type": "object",
                "properties": {"cmd": {"type": "string", "description": "Command, e.g. 'df -h' or 'ls -la'"}},
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create/overwrite a text file (under HOME or /tmp only, max 100KB). REQUIRES user approval. Use for scripts, notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, e.g. ~/notes/todo.md or /tmp/test.py"},
                    "content": {"type": "string", "description": "Full file content"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Exact-string edit of a text file (old_string must be unique). REQUIRES user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Save a short fact for future sessions (e.g. 'prefers qwen3:4b for speed'). No approval needed.",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string", "description": "Fact, max 500 chars"}},
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Search saved memories by keywords. Use when user asks 'what do you remember / my prefs'.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search terms"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_add",
            "description": "Add a todo (e.g. 'clean disk'). No approval needed.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_list",
            "description": "List open todos. Use when user asks 'my todos / what next'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_done",
            "description": "Mark a todo done by id.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "integer", "description": "Todo id"}},
                "required": ["id"],
            },
        },
    },
]


def dispatch_tool(name: str, args: dict) -> str:
    if name == "sysinfo":
        return tool_sysinfo()
    if name == "list_dir":
        return tool_list_dir(str(args.get("path", ".")))
    if name == "read_file":
        return tool_read_file(str(args.get("path", "")))
    if name == "exec":
        return tool_exec(str(args.get("cmd", "")))
    if name == "write_file":
        return tool_write_file(str(args.get("path", "")), str(args.get("content", "")))
    if name == "edit_file":
        return tool_edit_file(str(args.get("path", "")), str(args.get("old_string", "")), str(args.get("new_string", "")))
    if name == "remember":
        from .store import save_memory

        return save_memory(str(args.get("content", ""))[:2000])
    if name == "recall":
        from .store import recall_memories

        hits = recall_memories(str(args.get("query", "")), limit=5)
        return "\n".join(f"- {h}" for h in hits) if hits else "(no memories yet)"
    if name == "todo_add":
        from .store import add_todo

        return add_todo(str(args.get("text", ""))[:500])
    if name == "todo_list":
        from .store import list_todos

        rows = list_todos(open_only=True)
        return "\n".join(f"#{i}: {t}" for i, t, _ in rows) if rows else "(no open todos)"
    if name == "todo_done":
        from .store import complete_todo

        try:
            tid = int(args.get("id", 0))
        except Exception:
            return "Error: id must be int."
        return complete_todo(tid)
    return f"Error: unknown tool '{name}'"
