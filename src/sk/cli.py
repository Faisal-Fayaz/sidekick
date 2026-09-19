"""CLI: sk chat / sk run / sk models / sk doctor / sk config"""

from __future__ import annotations

import time
import uuid

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from .agent import get_client, run_agent
from .config import Config
from .store import get_history, save_message

app = typer.Typer(add_completion=False, help="Sidekick - local terminal companion (Ollama)")
console = Console()


def _cfg() -> Config:
    cfg = Config.load()
    cfg.ensure_created()
    return cfg


def _make_approver(auto_yes: bool):
    from .tools import WRITE_TOOLS

    def approve(name: str, args: dict) -> bool:
        if name not in WRITE_TOOLS:
            return True
        path = args.get("path", "?")
        preview = ""
        if name == "write_file":
            c = str(args.get("content", ""))
            preview = c[:600] + ("... [truncated]" if len(c) > 600 else "")
        else:
            old = str(args.get("old_string", ""))[:300]
            new = str(args.get("new_string", ""))[:300]
            preview = f"OLD:\n{old}\nNEW:\n{new}"
        console.print(Panel(f"[bold yellow]write approval[/] {name} -> [cyan]{path}[/cyan]\n{preview}", expand=False))
        if auto_yes:
            console.print("[dim]--yes: auto-approved[/dim]")
            return True
        try:
            return typer.confirm("Allow this write?", default=False)
        except (EOFError, KeyboardInterrupt):
            return False

    return approve


def _make_on_tool():
    def on_tool(name, args):
        # newline first since tokens stream without newlines
        console.print()
        console.print(f"[dim]○ tool: {name} {args if name not in ('write_file',) else {'path': args.get('path')}}[/dim]")

    return on_tool


def _make_on_token():
    import sys

    state = {"n": 0}

    def on_token(tok: str):
        state["n"] += len(tok)
        sys.stdout.write(tok)
        sys.stdout.flush()

    on_token.state = state  # type: ignore
    return on_token


def _resolve_model(cfg, model_opt: str) -> str:
    """--model > SIDEKICK_MODEL > config. Supports fast/smart aliases.

    fast = llama3.2:3b (2GB, instant on 4GB VRAM, non-reasoning)
    smart = qwen2.5-coder:7b (best tools/code, slower)
    qwen3:4b available explicitly by name (good balance, but thinks a lot).
    """
    if model_opt:
        m = model_opt.strip()
        if m == "fast":
            return "llama3.2:3b"
        if m == "smart":
            return "qwen2.5-coder:7b"
        return m
    return cfg.model


@app.command()
def chat(
    session: str = typer.Option("default", help="Session name for history"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve writes"),
    model: str = typer.Option("", help="Model override: name or fast/smart"),
    no_stream: bool = typer.Option(False, "--no-stream", help="Disable live token streaming"),
):
    """Interactive REPL: sk chat"""
    cfg = _cfg()
    cfg.model = _resolve_model(cfg, model)
    mode = "auto-approve writes" if yes else "confirm writes"
    console.print(Panel(f"[bold]sidekick[/]  model=[cyan]{cfg.model}[/]  session=[cyan]{session}[/]  {mode}\nType [bold]exit[/] to quit. Use [bold]@path[/] to attach a file.", expand=False))
    approve = _make_approver(yes)
    on_tool = _make_on_tool()
    on_token = None if no_stream else _make_on_token()
    while True:
        try:
            user = console.input("[bold green]you> [/]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nbye.")
            break
        if not user:
            continue
        if user.lower() in {"exit", "quit", ":q"}:
            console.print("bye.")
            break

        history = get_history(session)
        save_message(session, "user", user)

        console.print("[dim]thinking... (streams live)[/dim]")
        try:
            answer = run_agent(user, history, cfg, on_tool=on_tool, on_token=on_token, approve=approve)
        except Exception as e:
            console.print(f"[red]Error talking to Ollama ({cfg.base_url} model={cfg.model}): {e}[/red]")
            console.print("[dim]Tip: run `sk doctor` and `ollama serve`[/dim]")
            continue
        save_message(session, "assistant", answer)
        console.print()
        streamed = getattr(on_token, "state", {}).get("n", 0) if on_token else 0
        if on_token is None or streamed < len(answer or "") * 0.5:
            console.print(Markdown(answer or "(empty)"))
        console.print("[dim]--- done ---[/dim]")
        console.print()


@app.command()
def run(
    task: str = typer.Argument(..., help="Task in quotes, e.g. \"summarize disk usage\""),
    session: str = typer.Option("default", help="Session name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve writes (else prompts)"),
    model: str = typer.Option("", help="Model override: name or fast/smart"),
    no_stream: bool = typer.Option(False, "--no-stream", help="Disable live token streaming"),
):
    """Single-shot: sk run \"summarize disk usage in ~/\" """
    cfg = _cfg()
    cfg.model = _resolve_model(cfg, model)
    history = get_history(session)
    mode = "auto-approve writes" if yes else "confirm writes"
    console.print(f"[dim]task: {task}  model: {cfg.model} ({mode})[/dim]")
    save_message(session, "user", task)

    approve = _make_approver(yes)
    on_tool = _make_on_tool()
    on_token = None if no_stream else _make_on_token()

    console.print("[dim]working... (streams live)[/dim]")
    try:
        answer = run_agent(task, history, cfg, on_tool=on_tool, on_token=on_token, approve=approve)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    save_message(session, "assistant", answer)
    console.print()
    streamed = getattr(on_token, "state", {}).get("n", 0) if on_token else 0
    if on_token is None or streamed < len(answer or "") * 0.5:
        console.print(Markdown(answer or "(empty)"))
    console.print("[dim]--- done ---[/dim]")


@app.command()
def models():
    """List Ollama models."""
    import httpx

    cfg = _cfg()
    base = cfg.base_url.replace("/v1", "")
    try:
        r = httpx.get(f"{base}/api/tags", timeout=5)
        r.raise_for_status()
        data = r.json()
        names = [m["name"] for m in data.get("models", [])]
        if not names:
            console.print("[yellow]No models found. Try `ollama pull qwen3:4b`[/yellow]")
            return
        for n in names:
            mark = "← current" if n == cfg.model else ""
            console.print(f"• [cyan]{n}[/cyan] {mark}")
    except Exception as e:
        console.print(f"[red]Cannot reach Ollama at {base}: {e}[/red]")


@app.command()
def doctor():
    """Check Ollama + model + config health."""
    cfg = _cfg()
    console.print(f"config: [cyan]~/.sidekick/config.toml[/cyan] model=[cyan]{cfg.model}[/cyan] base=[cyan]{cfg.base_url}[/cyan]")
    import httpx

    base = cfg.base_url.replace("/v1", "")
    try:
        r = httpx.get(f"{base}/api/tags", timeout=5)
        r.raise_for_status()
        names = [m["name"] for m in r.json().get("models", [])]
        console.print(f"[green]✓ Ollama reachable[/green] ({len(names)} models)")
        if cfg.model in names:
            console.print(f"[green]✓ model '{cfg.model}' installed[/green]")
        else:
            console.print(f"[yellow]! model '{cfg.model}' not found. Run: ollama pull {cfg.model}[/yellow]")
    except Exception as e:
        console.print(f"[red]✗ Ollama not reachable: {e}[/red]")
        console.print("[dim]Run `ollama serve` in another terminal.[/dim]")
    # quick tool sanity
    from .tools import tool_exec, tool_list_dir

    console.print(f"[dim]tools sanity: {tool_exec('pwd')[:80]}[/dim]")


@app.command()
def config(
    model: str = typer.Option("", help="Set model, e.g. --model qwen3:4b"),
    show: bool = typer.Option(False, "--show", help="Show current config"),
):
    """View/set config."""
    cfg = _cfg()
    if model:
        cfg.model = model
        cfg.save()
        console.print(f"[green]model set to {model}[/green]")
    if show or not model:
        console.print(f"model={cfg.model}\nbase_url={cfg.base_url}\nmax_steps={cfg.max_steps}\ntemp={cfg.temperature}")


@app.command()
def remember(text: str = typer.Argument(..., help="Fact to save, e.g. 'prefers fast model'")):
    """Save a memory: sk remember \"prefers qwen3:4b\" """
    from .store import save_memory

    console.print(f"[green]{save_memory(text)}[/green] {text[:120]}")


@app.command(name="recall")
def recall_cmd(query: str = typer.Argument("", help="Search terms (empty = recent)")):
    """Search memories: sk recall \"model\" """
    from .store import recall_memories

    hits = recall_memories(query, limit=10)
    if not hits:
        console.print("[yellow](no memories yet — try `sk remember \"...\"`)[/yellow]")
        return
    for h in hits:
        console.print(f"• {h}")


@app.command(name="memories")
def memories_cmd():
    """List all memories."""
    from .store import list_memories

    hits = list_memories(limit=50)
    if not hits:
        console.print("[yellow](no memories yet)[/yellow]")
        return
    for h in hits:
        console.print(f"• {h}")


@app.command()
def forget(query: str = typer.Argument(..., help="Substring to delete")):
    """Delete matching memories: sk forget \"qwen\" """
    from .store import forget_memory

    console.print(f"[yellow]{forget_memory(query)}[/yellow]")


todo_app = typer.Typer(help="Todos: sk todo add/list/done/clear")
app.add_typer(todo_app, name="todo")


@todo_app.command("add")
def todo_add(text: str = typer.Argument(..., help="Todo text")):
    """Add: sk todo add \"clean disk\" """
    from .store import add_todo

    console.print(f"[green]{add_todo(text)}[/green]")


@todo_app.command("list")
def todo_list(all: bool = typer.Option(False, "--all", help="Include done")):
    """List: sk todo list """
    from .store import list_todos

    rows = list_todos(open_only=not all)
    if not rows:
        console.print("[dim](no todos — sk todo add \"...\" )[/dim]")
        return
    for i, t, d in rows:
        mark = "[green]✓[/green]" if d else "[yellow]○[/yellow]"
        console.print(f"{mark} #{i} {t}")


@todo_app.command("done")
def todo_done(tid: int = typer.Argument(..., help="Todo id")):
    """Done: sk todo done 1 """
    from .store import complete_todo

    console.print(f"[green]{complete_todo(tid)}[/green]")


@todo_app.command("clear")
def todo_clear():
    """Clear done: sk todo clear """
    from .store import clear_todos

    console.print(f"[yellow]{clear_todos()}[/yellow]")


BASH_SNIPPET = """# sidekick shell hook — logs commands for `sk history` / `sk oops`
_sk_hook() {
  local rc=$?
  local cmd=$(HISTTIMEFORMAT= history 1 | sed 's/^[ ]*[0-9]*[ ]*//')
  SK_BIN="${SK_BIN:-$HOME/.local/bin/sk}"
  [ -x "$SK_BIN" ] && "$SK_BIN" hook-log --cmd "$cmd" --exit "$rc" --cwd "$PWD" >/dev/null 2>&1
  return $rc
}
case "$PROMPT_COMMAND" in *_sk_hook*) ;; *) PROMPT_COMMAND="_sk_hook;${PROMPT_COMMAND:-:}";; esac
"""

ZSH_SNIPPET = """# sidekick shell hook — logs commands for `sk history` / `sk oops`
_sk_hook() {
  local rc=$?
  SK_BIN="${SK_BIN:-$HOME/.local/bin/sk}"
  [ -x "$SK_BIN" ] && "$SK_BIN" hook-log --cmd "$1" --exit "$rc" --cwd "$PWD" >/dev/null 2>&1
  return $rc
}
autoload -Uz add-zsh-hook
_sk_log_preexec() { _SK_CMD="$1"; }
_sk_log_precmd() { local rc=$?; _sk_hook "$_SK_CMD"; }
add-zsh-hook preexec _sk_log_preexec
add-zsh-hook precmd _sk_log_precmd
"""


@app.command(name="hook-log", hidden=True)
def hook_log(
    cmd: str = typer.Option("", "--cmd", help="Command line"),
    exit: int = typer.Option(0, "--exit", help="Exit code"),
    cwd: str = typer.Option("", "--cwd", help="Working dir"),
):
    """Internal: called by shell hook. Not for manual use."""
    from .store import log_shell

    log_shell(cmd, cwd, exit)


@app.command()
def history(limit: int = typer.Option(15, "--limit", "-n", help="Rows to show")):
    """Show recent shell commands: sk history """
    from .store import list_shell

    rows = list_shell(limit=limit)
    if not rows:
        console.print("[dim](no shell history yet — run `sk hook-install`)[/dim]")
        return
    for i, cmd, cwd, rc in reversed(rows):
        mark = f"[red]✗{rc}[/red]" if rc else "[green]✓[/green]"
        console.print(f"{mark} {cmd[:120]}  [dim]{cwd[-40:]}[/dim]")


@app.command()
def oops(
    model: str = typer.Option("", help="Model override or fast/smart"),
    no_stream: bool = typer.Option(False, "--no-stream", help="Disable streaming"),
):
    """Explain last failed command: sk oops """
    from .store import last_failed

    fail = last_failed()
    if not fail:
        console.print("[green]No failures logged. Clean shell.[/green]")
        return
    _, cmd, cwd, rc = fail
    cfg = _cfg()
    cfg.model = _resolve_model(cfg, model)
    from .store import get_history

    from .agent import run_agent

    console.print(f"[dim]last failure (exit {rc}): {cmd} @ {cwd}[/dim]")
    console.print("[dim]working... (streams live)[/dim]")
    on_token = None if no_stream else _make_on_token()
    try:
        answer = run_agent(
            f"My last shell command failed with exit {rc} in {cwd}: `{cmd}`. Explain the likely cause in 2 lines and give the exact fixed command. No fluff.",
            get_history("oops")[-5:],
            cfg,
            on_tool=_make_on_tool(),
            on_token=on_token,
            approve=_make_approver(True),
        )
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    save_message("oops", "user", cmd)
    save_message("oops", "assistant", answer)
    console.print()
    streamed = getattr(on_token, "state", {}).get("n", 0) if on_token else 0
    if on_token is None or streamed < len(answer or "") * 0.5:
        console.print(Markdown(answer or "(empty)"))
    console.print("[dim]--- done ---[/dim]")


@app.command(name="hook-install")
def hook_install(
    shell: str = typer.Option("", help="bash or zsh (auto-detect)"),
    write: bool = typer.Option(False, "--write", help="Append to rc file"),
):
    """Print shell hook. Use --write to append to ~/.bashrc or ~/.zshrc."""
    import os
    from pathlib import Path

    sh = (shell or os.path.basename(os.getenv("SHELL", "bash"))).lower()
    snippet = ZSH_SNIPPET if "zsh" in sh else BASH_SNIPPET
    rc = Path.home() / (".zshrc" if "zsh" in sh else ".bashrc")

    console.print(Panel(snippet, title=f"hook for {sh} -> {rc}", expand=False))
    if not write:
        console.print(f"[dim]re-run with `sk hook-install --write` to append to {rc}[/dim]")
        return
    text = rc.read_text() if rc.exists() else ""
    if "_sk_hook" in text:
        console.print("[yellow]hook already installed.[/yellow]")
        return
    with open(rc, "a") as f:
        f.write("\n" + snippet)
    console.print(f"[green]appended to {rc}. Restart shell or `source {rc}`.[/green]")


@app.command()
def brief(
    project: list[str] = typer.Option([], "--project", "-p", help="Extra project path (repeatable)"),
    smart: bool = typer.Option(False, "--smart", help="Pipe digest through LLM for 2-line summary"),
    model: str = typer.Option("", help="Model for --smart (or fast/smart)"),
):
    """Morning digest: system + git + memories. Instant, no LLM unless --smart."""
    from rich.table import Table

    from .brief import DEFAULT_PROJECTS, gather_brief

    projs = DEFAULT_PROJECTS + list(project or [])
    data = gather_brief(projs)
    console.print(Panel(f"[bold]sidekick brief[/]  {data['when']}", expand=False))

    # system: trim sysinfo to essentials
    sysinfo: str = data["sysinfo"]
    # show RAM/GPU/disk lines only to stay short
    keep: list[str] = []
    for line in sysinfo.splitlines():
        ll = line.lower()
        if line.startswith("CPU:") or line.startswith("GPU:") or "mem:" in ll or "memory" in ll or "/dev/nvme" in line or "OLLAMA MODELS" in line or "qwen" in line or "llama" in line or "NAME " in line:
            keep.append(line)
    console.print(Panel("\n".join(keep[:14]) or sysinfo[:1000], title="system", expand=False))

    table = Table(title="projects", expand=False)
    table.add_column("project")
    table.add_column("branch")
    table.add_column("changed")
    table.add_column("last commits")
    for s in data["projects"]:
        if not s.get("exists"):
            table.add_row(s["path"], "-", "-", "missing")
            continue
        log_one = (s.get("log") or "-").splitlines()
        log_one = log_one[0][:60] if log_one else "-"
        changed = str(s.get("changed", 0))
        table.add_row(s["path"], s.get("branch", "?")[:20], changed, log_one)
    console.print(table)

    # deterministic warnings (no LLM needed)
    import re

    warns: list[str] = []
    m = re.search(r"(\d+)% /", sysinfo)
    if m and int(m.group(1)) >= 90:
        warns.append(f"disk {m.group(1)}% full — clean ~/Downloads, docker, ollama blobs")
    for s in data["projects"]:
        if s.get("exists") and int(s.get("changed", 0) or 0) > 0:
            warns.append(f"{s['path']}: {s['changed']} uncommitted changes")
    if warns:
        console.print(Panel("\n".join(f"! {w}" for w in warns), title="warnings", expand=False))

    mems: list = data.get("memories", [])
    if mems:
        console.print(Panel("\n".join(f"• {m}" for m in mems[:5]), title="memories", expand=False))
    else:
        console.print("[dim]no memories yet — sk remember \"...\"[/dim]")

    todos: list = data.get("todos", [])
    if todos:
        console.print(Panel("\n".join(f"○ #{i} {t}" for i, t, _ in todos[:5]), title="open todos", expand=False))

    if smart:
        cfg = _cfg()
        cfg.model = _resolve_model(cfg, model)
        from .agent import run_agent
        from .store import get_history

        digest = f"SYSTEM:\n{sysinfo[:1500]}\nPROJECTS:\n{data['projects']}\nMEMORIES:\n{mems[:5]}"
        console.print("[dim]summarizing...[/dim]")
        ans = run_agent(
            f"Give a 3-bullet morning brief from this digest. Flag disk>90%, dirty git repos, and what to work on first.\n{digest[:4000]}",
            get_history("default")[-5:],
            cfg,
            on_tool=_make_on_tool(),
            on_token=_make_on_token(),
            approve=_make_approver(True),
        )
        console.print()
        console.print("[dim]--- done ---[/dim]")


@app.command()
def tui(
    model: str = typer.Option("", help="Model override or fast/smart"),
):
    """Fullscreen dashboard: brief + todos + memories + chat."""
    from .tui import launch

    cfg = _cfg()
    launch(_resolve_model(cfg, model))


if __name__ == "__main__":
    app()
