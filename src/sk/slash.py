"""Slash commands shared by `sk chat` REPL and TUI. Hermes-style: /help /model /clear ..."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SlashOut:
    handled: bool = False
    text: str = ""  # render this to the user
    agent_prompt: str = ""  # if set, caller should send this to run_agent instead
    clear_view: bool = False  # caller should clear visible log
    quit: bool = False


HELP_TEXT = """**slash commands**
- `/help` — this list
- `/model [fast|smart|name]` — show or switch model
- `/models` — list installed Ollama models
- `/clear` — fresh session (forgets chat history)
- `/yolo` — auto-approve file writes
- `/confirm` — ask before file writes (default in TUI)
- `/remember <fact>` — save a memory
- `/recall [words]` — search memories
- `/memories` — list all memories
- `/forget <words>` — delete matching memories
- `/todo add <text>` — add a todo
- `/todo list` — open todos
- `/todo done <id>` — complete a todo
- `/todo clear` — clear done todos
- `/brief` — morning digest (system + git + todos + memories)
- `/history [n]` — recent shell commands
- `/oops` — explain last failed shell command
- `/skills` — list skill packs
- `/exit` `/quit` — leave
Anything else is sent to the agent."""


def _resolve_model_name(cfg, raw: str) -> str:
    m = (raw or "").strip()
    if m == "fast":
        return "llama3.2:3b"
    if m == "smart":
        return "qwen2.5-coder:7b"
    return m


def handle(text: str, *, session: str, cfg, state: dict) -> SlashOut:
    """Dispatch a /command. state['yolo'] is mutable approve mode."""
    if not text.startswith("/"):
        return SlashOut(handled=False)
    parts = text[1:].split(None, 1)
    cmd = (parts[0] or "").lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("help", "h", "?"):
        return SlashOut(handled=True, text=HELP_TEXT)

    if cmd == "model":
        if not arg:
            return SlashOut(handled=True, text=f"model: `{cfg.model}` — switch with `/model fast|smart|<name>`")
        cfg.model = _resolve_model_name(cfg, arg)
        try:
            cfg.save()
        except Exception:
            pass
        return SlashOut(handled=True, text=f"model → `{cfg.model}`")

    if cmd == "models":
        import httpx

        base = cfg.base_url.replace("/v1", "")
        try:
            r = httpx.get(f"{base}/api/tags", timeout=5)
            names = [m["name"] for m in r.json().get("models", [])]
        except Exception as e:
            return SlashOut(handled=True, text=f"Ollama unreachable: {e}")
        lines = [f"- {n}{' ← current' if n == cfg.model else ''}" for n in names]
        return SlashOut(handled=True, text="\n".join(lines) or "(no models)")

    if cmd == "clear":
        from .store import clear_session

        clear_session(session)
        return SlashOut(handled=True, text="_session cleared_", clear_view=True)

    if cmd == "yolo":
        state["yolo"] = True
        return SlashOut(handled=True, text="_writes auto-approved now (`/confirm` to revert)_")

    if cmd == "confirm":
        state["yolo"] = False
        return SlashOut(handled=True, text="_will ask before file writes_")

    if cmd == "remember":
        if not arg.strip():
            return SlashOut(handled=True, text="usage: `/remember <fact>`")
        from .store import save_memory

        return SlashOut(handled=True, text=f"{save_memory(arg.strip()[:2000])} _{arg.strip()[:120]}_")

    if cmd == "recall":
        from .store import recall_memories

        hits = recall_memories(arg, limit=10)
        return SlashOut(handled=True, text="\n".join(f"- {h}" for h in hits) if hits else "_(no memories yet)_")

    if cmd == "memories":
        from .store import list_memories

        hits = list_memories(limit=50)
        return SlashOut(handled=True, text="\n".join(f"- {h}" for h in hits) if hits else "_(no memories yet)_")

    if cmd == "forget":
        from .store import forget_memory

        return SlashOut(handled=True, text=forget_memory(arg) if arg else "usage: `/forget <words>`")

    if cmd == "todo":
        sub, _, rest = arg.partition(" ")
        sub = sub.lower()
        from .store import add_todo, clear_todos, complete_todo, list_todos

        if sub == "add" and rest.strip():
            return SlashOut(handled=True, text=add_todo(rest.strip()[:500]))
        if sub in ("list", "ls", ""):
            rows = list_todos(open_only=True)
            return SlashOut(handled=True, text="\n".join(f"○ #{i} {t}" for i, t, _ in rows) if rows else "_(no open todos)_")
        if sub == "done" and rest.strip():
            try:
                return SlashOut(handled=True, text=complete_todo(int(rest.strip().split()[0])))
            except ValueError:
                return SlashOut(handled=True, text="usage: `/todo done <id>`")
        if sub == "clear":
            return SlashOut(handled=True, text=clear_todos())
        return SlashOut(handled=True, text="usage: `/todo add|list|done|clear`")

    if cmd == "brief":
        from .brief import format_brief_text, gather_brief

        return SlashOut(handled=True, text=format_brief_text(gather_brief()))

    if cmd == "history":
        try:
            n = int((arg.strip().split() or ["15"])[0])
        except ValueError:
            n = 15
        from .store import list_shell

        rows = list_shell(limit=max(1, min(n, 50)))
        if not rows:
            return SlashOut(handled=True, text="_(no shell history — run `sk hook-install`)_")
        lines = [f"{'✗'+str(rc) if rc else '✓'} `{c[:100]}`" for _, c, _, rc in reversed(rows)]
        return SlashOut(handled=True, text="\n".join(lines))

    if cmd == "oops":
        from .store import last_failed

        fail = last_failed()
        if not fail:
            return SlashOut(handled=True, text="_No failures logged. Clean shell._")
        _, fcmd, fcwd, rc = fail
        prompt = (
            f"My last shell command failed with exit {rc} in {fcwd}: `{fcmd}`. "
            "Explain the likely cause in 2 lines and give the exact fixed command. No fluff."
        )
        return SlashOut(handled=True, agent_prompt=prompt)

    if cmd == "skills":
        from .skills import SKILLS_DIR, list_skills

        rows = list_skills()
        lines = [f"- **{n}** ({s}b)" for n, s in rows]
        return SlashOut(handled=True, text=f"`{SKILLS_DIR}`\n" + ("\n".join(lines) if lines else "(none)"))

    if cmd in ("exit", "quit", "q"):
        return SlashOut(handled=True, quit=True)

    return SlashOut(handled=True, text=f"unknown command `/{cmd}` — try `/help`")
