"""Slash commands shared by `sk chat` REPL and TUI. Hermes-style: /help /model /clear ..."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SlashOut:
    handled: bool = False
    text: str = ""  # render this to the user
    agent_prompt: str = ""  # if set, caller should send this to run_agent instead
    clear_view: bool = False  # caller should clear visible log
    quit: bool = False
    switch_session: str = ""  # caller should adopt this session going forward


COMMANDS: list[tuple[str, str]] = [
    ("help", "this list"),
    ("model [fast|smart|name]", "show or switch model (`sk model` for guided picker)"),
    ("provider [name]", "show or switch provider (keys via `sk auth add`, never pasted here)"),
    ("models", "list models on the current provider"),
    ("clear", "start a fresh session (old one kept, see `/sessions`)"),
    ("sessions [delete <n>]", "list past sessions, or delete one"),
    ("resume <n>", "switch to a past session"),
    ("yolo", "auto-approve file writes"),
    ("confirm", "ask before file writes (default in TUI)"),
    ("remember <fact>", "save a memory"),
    ("recall [words]", "search memories"),
    ("memories", "list all memories"),
    ("forget <words>", "delete matching memories"),
    ("todo add <text>", "add a todo"),
    ("todo list", "open todos"),
    ("todo done <id>", "complete a todo"),
    ("todo clear", "clear done todos"),
    ("brief", "morning digest (system + git + todos + memories)"),
    ("history [n]", "recent shell commands"),
    ("oops", "explain last failed shell command"),
    ("skills", "list skill packs"),
    ("copy [n]", "copy nth-last answer (default: last)"),
    ("copy lines <n>", "copy last n lines of the last answer (for code blocks)"),
    ("exit", "leave (also `/quit`)"),
]

HELP_TEXT = (
    "**slash commands**\n"
    + "\n".join(f"- `/{name}` — {desc}" for name, desc in COMMANDS)
    + "\nAnything else is sent to the agent.\n"
    + "Tip: paste with Ctrl+Shift+V (terminal). Mouse drag-select is terminal-dependent; `/copy lines` always works."
)


def _resolve_model_name(cfg, raw: str) -> str:
    from .config import resolve_alias

    return resolve_alias(cfg.provider, raw, cfg.model)


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
        from .auth import fetch_models

        try:
            names = fetch_models(cfg.provider, cfg.effective_base_url(), cfg.effective_api_key())
        except Exception as e:
            return SlashOut(handled=True, text=f"{cfg.provider} unreachable: {e}")
        lines = [f"- {n}{' ← current' if n == cfg.model else ''}" for n in names[:40]]
        return SlashOut(handled=True, text="\n".join(lines) or "(no models)")

    if cmd == "provider":
        from .config import PRESETS

        if not arg.strip():
            return SlashOut(handled=True, text=f"provider: `{cfg.provider}` — switch: `/provider {'|'.join(PRESETS)}`")
        p = arg.strip().lower()
        if p not in PRESETS:
            return SlashOut(handled=True, text=f"unknown provider. Pick: {', '.join(PRESETS)}")
        cfg.provider = p
        cfg.model = PRESETS[p]["model"] or cfg.model
        cfg.base_url = ""
        try:
            cfg.save()
        except Exception:
            pass
        return SlashOut(handled=True, text=f"provider → `{p}` model → `{cfg.model}` (key via SIDEKICK_API_KEY or `sk config --api-key …`)")

    if cmd == "clear":
        from .store import new_session_id

        fresh = new_session_id(session.split("-")[0] if "-" in session else session)
        return SlashOut(handled=True, text=f"_fresh session `{fresh}`_", clear_view=True, switch_session=fresh)

    def _session_lines() -> tuple[list[dict], list[str]]:
        import datetime as _dt

        from .store import list_sessions

        rows = list_sessions(limit=20)
        lines = []
        for i, r in enumerate(rows, 1):
            when = _dt.datetime.fromtimestamp(r["last_ts"]).strftime("%m-%d %H:%M") if r["last_ts"] else "?"
            cur = " ← current" if r["session"] == session else ""
            lines.append(f"{i}. `{r['session']}` · {r['count']} msgs · {r['preview'] or '(empty)'} · {when}{cur}")
        return rows, lines

    if cmd == "sessions":
        from .store import delete_session

        sub, _, rest = arg.partition(" ")
        if sub.strip().lower() == "delete":
            rows, _ = _session_lines()
            try:
                idx = int(rest.strip().split()[0]) - 1
                target = rows[idx]["session"]
            except (ValueError, IndexError):
                return SlashOut(handled=True, text="usage: `/sessions delete <n>` (see `/sessions`)")
            n = delete_session(target)
            return SlashOut(handled=True, text=f"_deleted `{target}` ({n} messages)_")
        rows, lines = _session_lines()
        if not lines:
            return SlashOut(handled=True, text="_(no past sessions yet)_")
        return SlashOut(handled=True, text="**sessions**\n" + "\n".join(lines) + "\n`/resume <n>` to switch · `/sessions delete <n>` to remove")

    if cmd == "resume":
        rows, _ = _session_lines()
        try:
            target = rows[int(arg.strip().split()[0]) - 1]["session"]
        except (ValueError, IndexError):
            return SlashOut(handled=True, text="usage: `/resume <n>` (see `/sessions`)")
        if target == session:
            return SlashOut(handled=True, text=f"_already on `{target}`_")
        return SlashOut(handled=True, text=f"_resumed `{target}`_", clear_view=True, switch_session=target)

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

    if cmd == "copy":
        from .clip import backends_available, copy_text, install_hint
        from .store import get_history

        parts = arg.strip().split()
        if parts and parts[0].lower() == "lines":
            try:
                n = int(parts[1]) if len(parts) > 1 else 10
            except ValueError:
                return SlashOut(handled=True, text="usage: `/copy lines <n>`")
            answers = [m["content"] for m in get_history(session) if m["role"] == "assistant"]
            if not answers:
                return SlashOut(handled=True, text="_(no answers to copy yet)_")
            tail = "\n".join(answers[-1].splitlines()[-max(1, n):])
            if not tail.strip():
                return SlashOut(handled=True, text="_(last answer is empty)_")
            try:
                method = copy_text(tail)
            except Exception as e:
                return SlashOut(handled=True, text=f"copy failed ({e}) — `{install_hint()}`")
            extra = f" — `{install_hint()}` if paste comes up empty" if method == "osc52" and not backends_available() else ""
            return SlashOut(handled=True, text=f"_copied last {max(1, n)} lines via {method}_{extra}")
        try:
            n = int((parts or ["1"])[0])
        except ValueError:
            return SlashOut(handled=True, text="usage: `/copy [n]` — copies nth-last answer")
        answers = [m["content"] for m in get_history(session) if m["role"] == "assistant"]
        if not answers or n < 1 or n > len(answers):
            return SlashOut(handled=True, text="_(no answers to copy yet)_")
        try:
            method = copy_text(answers[-n])
        except Exception as e:
            return SlashOut(handled=True, text=f"copy failed ({e}) — `{install_hint()}`")
        extra = f" — `{install_hint()}` if paste comes up empty" if method == "osc52" and not backends_available() else ""
        return SlashOut(handled=True, text=f"_copied answer {-n if n > 1 else 'last'} via {method}_{extra}")

    return SlashOut(handled=True, text=f"unknown command `/{cmd}` — try `/help`")
