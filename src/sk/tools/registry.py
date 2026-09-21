"""Tools submodule: tool schemas, approval set, dispatcher. (split from sk/tools.py, pure move)."""

from __future__ import annotations

from .read import tool_exec, tool_list_dir, tool_read_file, tool_sysinfo
from .shell import tool_shell
from .web import tool_read_url, tool_web_search
from .write import (
    WRITE_TOOLS,
    tool_delete_file,
    tool_edit_file,
    tool_make_dir,
    tool_write_file,
)

# everything requiring user approval (writes + general shell + delete)
APPROVAL_TOOLS = WRITE_TOOLS | {"shell", "delete_file"}

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
            "description": "Run a READ-ONLY shell command (ls, df, du, git status/log, pwd, etc). No pipes/redirects. No approval needed.",
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
            "name": "shell",
            "description": "Run ANY shell command (pipes, installs, git push, scripts...). REQUIRES user approval. Destructive patterns (rm -rf /, mkfs, dd to disks) are refused outright.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Full bash command"},
                    "timeout": {"type": "integer", "description": "Seconds, default 30, max 120"},
                },
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file or EMPTY dir under HOME/tmp. REQUIRES user approval. Non-empty dirs refused.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
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
            "name": "make_dir",
            "description": "Create a directory (like mkdir -p, HOME or /tmp only). REQUIRES user approval.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory to create, e.g. ~/notes"}},
                "required": ["path"],
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
    {
        "type": "function",
        "function": {
            "name": "read_url",
            "description": "Fetch a public http/https URL and return title + text (for docs, GitHub, articles). No localhost/private IPs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "https://..."},
                    "max_chars": {"type": "integer", "description": "Max chars, default 6000"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web (DuckDuckGo, no key). Use FIRST for 'search the internet / latest / most used right now' questions, then read_url the best hits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms"},
                    "count": {"type": "integer", "description": "Results, default 5, max 8"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill",
            "description": "Load full instructions of one skill pack by name (see SKILL INDEX in prompt, e.g. 'brainstorming'). Use when the task matches a skill's description.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Skill name from the index"}},
                "required": ["name"],
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
    if name == "shell":
        try:
            timeout = int(args.get("timeout", 30) or 30)
        except Exception:
            timeout = 30
        return tool_shell(str(args.get("cmd", "")), timeout)
    if name == "delete_file":
        return tool_delete_file(str(args.get("path", "")))
    if name == "write_file":
        return tool_write_file(str(args.get("path", "")), str(args.get("content", "")))
    if name == "edit_file":
        return tool_edit_file(str(args.get("path", "")), str(args.get("old_string", "")), str(args.get("new_string", "")))
    if name == "make_dir":
        return tool_make_dir(str(args.get("path", "")))
    if name == "remember":
        from sk.store import save_memory

        return save_memory(str(args.get("content", ""))[:2000])
    if name == "recall":
        from sk.store import recall_memories

        hits = recall_memories(str(args.get("query", "")), limit=5)
        return "\n".join(f"- {h}" for h in hits) if hits else "(no memories yet)"
    if name == "todo_add":
        from sk.store import add_todo

        return add_todo(str(args.get("text", ""))[:500])
    if name == "todo_list":
        from sk.store import list_todos

        rows = list_todos(open_only=True)
        return "\n".join(f"#{i}: {t}" for i, t, _ in rows) if rows else "(no open todos)"
    if name == "todo_done":
        from sk.store import complete_todo

        try:
            tid = int(args.get("id", 0))
        except Exception:
            return "Error: id must be int."
        return complete_todo(tid)
    if name == "read_url":
        return tool_read_url(str(args.get("url", "")), int(args.get("max_chars", 6000) or 6000))
    if name == "web_search":
        return tool_web_search(str(args.get("query", "")), int(args.get("count", 5) or 5))
    if name == "skill":
        from sk.skills import show_skill

        return show_skill(str(args.get("name", "")))
    return f"Error: unknown tool '{name}'"
