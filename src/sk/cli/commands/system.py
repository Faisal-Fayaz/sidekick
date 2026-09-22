"""CLI commands: version/models/doctor/config/model/connect/setup/history/oops/hooks/brief (split from sk/cli.py, pure move)."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markdown import Markdown
from rich.panel import Panel

from sk.agent import run_agent
from sk.config import Config
from sk.store import get_history, save_message

from ..approvers import _make_approver, _make_on_token, _make_on_tool
from ..base import _cfg, app, console
from ..resolve import _resolve_model
from .auth import _connect_flow, _pick_model_name, _pick_provider


def _code_version() -> str:
    """Short git hash of the running checkout (dev) or package version."""
    try:
        import subprocess as _sp

        r = _sp.run(
            [
                "git",
                "-C",
                str(Path(__file__).resolve().parent.parent.parent),
                "rev-parse",
                "--short",
                "HEAD",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        h = (r.stdout or "").strip()
        if h:
            return h
    except Exception:
        pass
    try:
        from sk import __version__

        return __version__
    except Exception:
        return "unknown"


@app.command()
def version():
    """Show running code version (git hash). Compare with TUI header."""
    console.print(f"sk {_code_version()}")


@app.command()
def models():
    """List models for the current provider."""
    from sk.auth import fetch_models

    cfg = _cfg()
    try:
        names = fetch_models(cfg.provider, cfg.effective_base_url(), cfg.effective_api_key())
    except Exception as e:
        console.print(f"[red]Cannot reach {cfg.provider}: {e}[/red]")
        return
    if not names:
        console.print("[yellow]No models listed.[/yellow]")
        if cfg.provider == "ollama":
            console.print("[dim]Try `ollama pull qwen3:4b`[/dim]")
        return
    for n in names[:40]:
        mark = "← current" if n == cfg.model else ""
        console.print(f"• [cyan]{n}[/cyan] {mark}")
    if len(names) > 40:
        console.print(f"[dim]...+{len(names) - 40} more[/dim]")


@app.command()
def doctor():
    """Check provider + model + config health."""
    from sk.auth import provider_status

    cfg = _cfg()
    console.print(
        f"provider=[cyan]{cfg.provider}[/cyan] model=[cyan]{cfg.model}[/cyan] base=[cyan]{cfg.effective_base_url()}[/cyan] key=[cyan]{Config.mask(cfg.effective_api_key())}[/cyan] code=[cyan]{_code_version()}[/cyan]"
    )
    ok, msg = provider_status(cfg)
    if ok and cfg.provider in ("ollama", "lmstudio"):
        from sk.auth import fetch_models

        try:
            names = fetch_models(cfg.provider, cfg.effective_base_url(), cfg.effective_api_key())
            console.print(f"[green]✓ {cfg.provider} reachable[/green] ({len(names)} models)")
            if cfg.model in names:
                console.print(f"[green]✓ model '{cfg.model}' installed[/green]")
            else:
                console.print(
                    f"[yellow]! model '{cfg.model}' not found. Run: ollama pull {cfg.model}[/yellow]"
                )
        except Exception as e:
            console.print(f"[red]✗ {cfg.provider} not reachable: {e}[/red]")
    elif ok:
        console.print(f"[green]✓ {cfg.provider} reachable[/green] ({msg})")
    else:
        console.print(f"[red]✗ {msg}[/red]")
        if cfg.provider == "ollama":
            console.print("[dim]Run `ollama serve` in another terminal.[/dim]")
    # quick tool sanity
    from sk.tools import tool_exec

    console.print(f"[dim]tools sanity: {tool_exec('pwd')[:80]}[/dim]")


@app.command()
def config(
    model: str = typer.Option("", help="Set model, e.g. --model qwen3:4b"),
    provider: str = typer.Option(
        "",
        help="Set provider: ollama|openai|groq|together|deepseek|openrouter|google|lmstudio|anthropic|custom",
    ),
    api_key: str = typer.Option("", help="Set API key (or use SIDEKICK_API_KEY env)"),
    base_url: str = typer.Option(
        "", help="Custom base URL (sets provider=custom unless --provider given)"
    ),
    show: bool = typer.Option(False, "--show", help="Show current config (key masked)"),
):
    """View/set config. Keys are chmod-600’d; env vars always win."""
    from sk.config import PRESETS

    cfg = _cfg()
    changed = False
    if provider:
        p = provider.strip().lower()
        if p not in PRESETS:
            console.print(f"[red]unknown provider. Pick: {', '.join(PRESETS)}[/red]")
            raise typer.Exit(1)
        cfg.provider = p
        if not model:
            cfg.model = PRESETS[p]["model"] or cfg.model
        if not base_url:
            cfg.base_url = ""  # drop stale override, use preset default
        changed = True
        console.print(f"[green]provider set to {p}[/green]")
    if base_url:
        cfg.base_url = base_url.strip()
        if not provider:
            cfg.provider = "custom"
        changed = True
    if api_key:
        cfg.api_key = api_key.strip()
        changed = True
        console.print("[green]api key saved (file is chmod 600)[/green]")
    if model:
        cfg.model = model
        changed = True
        console.print(f"[green]model set to {model}[/green]")
    if changed:
        cfg.save()
    if show or not changed:
        console.print(
            f"provider={cfg.provider}\nmodel={cfg.model}\nbase_url={cfg.effective_base_url()}\napi_key={Config.mask(cfg.effective_api_key())}\nmax_steps={cfg.max_steps}\ntemp={cfg.temperature}"
        )
        if cfg.project_note():
            console.print(f"[dim]{cfg.project_note()}[/dim]")
        for w in cfg.project_warnings:
            console.print(f"[yellow]! {w}[/yellow]")


@app.command()
def model():
    """Interactive picker: provider → live model list → default."""
    from sk.auth import chat_models, fetch_models
    from sk.config import PRESETS

    cfg = _cfg()
    p = _pick_provider(cfg.provider)
    try:
        names = fetch_models(
            p, PRESETS[p]["base_url"], cfg.effective_api_key() if p == cfg.provider else ""
        )
    except Exception as e:
        console.print(f"[red]cannot list {p}: {e}[/red]")
        try:
            manual = console.input("Model id (manual entry): ").strip()
        except (EOFError, KeyboardInterrupt):
            raise typer.Exit(1)
        if not manual:
            raise typer.Exit(1)
        cfg.provider, cfg.model = p, manual
        cfg.save()
        console.print(f"[green]model set to {manual} (unvalidated)[/green]")
        return
    pick = _pick_model_name(p, chat_models(names), cfg)
    cfg.provider, cfg.model = p, pick
    if p != "custom":
        cfg.base_url = ""
    cfg.save()
    console.print(f"[green]default → {p} / {pick}[/green]")


@app.command()
def connect():
    """Connect a provider: pick → key → model → ping. The one-command setup."""
    from sk.auth import ping

    cfg = _connect_flow()
    console.print("[dim]ping...[/dim]")
    ok, msg = ping(cfg.provider, cfg.effective_base_url(), cfg.effective_api_key(), cfg.model)
    console.print(
        f"[green]✓ {cfg.provider} / {cfg.model} answers: {msg}[/green]"
        if ok
        else f"[red]✗ ping failed: {msg}[/red]"
    )
    if not ok:
        raise typer.Exit(1)
    console.print(
        Panel(
            f"[bold]connected[/]  provider=[cyan]{cfg.provider}[/]  model=[cyan]{cfg.model}[/]  key=[cyan]{Config.mask(cfg.effective_api_key())}[/]\nTry `sk tui`.",
            expand=False,
        )
    )


@app.command()
def setup():
    """Full setup: connect flow + shell hook. (For just keys: `sk connect`.)"""
    from sk.auth import ping

    console.print(Panel("[bold]sidekick setup[/] — connect, then hook.", expand=False))
    cfg = _connect_flow()
    try:
        if typer.confirm("Install shell hook (logs commands for history/oops)?", default=False):
            hook_install(shell="", write=True)
    except (EOFError, KeyboardInterrupt):
        pass
    console.print("[dim]ping...[/dim]")
    ok, msg = ping(cfg.provider, cfg.effective_base_url(), cfg.effective_api_key(), cfg.model)
    console.print(f"[green]✓ answers: {msg}[/green]" if ok else f"[red]✗ ping failed: {msg}[/red]")
    if not ok:
        raise typer.Exit(1)
    console.print("[green]setup complete. Try `sk tui`.[/green]")


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
    from sk.store import log_shell

    log_shell(cmd, cwd, exit)


@app.command()
def history(limit: int = typer.Option(15, "--limit", "-n", help="Rows to show")):
    """Show recent shell commands: sk history"""
    from sk.store import list_shell

    rows = list_shell(limit=limit)
    if not rows:
        console.print("[dim](no shell history yet — run `sk hook-install`)[/dim]")
        return
    for i, cmd, cwd, rc in reversed(rows):
        mark = f"[red]✗{rc}[/red]" if rc else "[green]✓[/green]"
        console.print(f"{mark} {cmd[:120]}  [dim]{cwd[-40:]}[/dim]")


@app.command()
def export(
    session: str = typer.Argument("", help="Session id (omit for latest)"),
    out: str = typer.Option("", "--out", help="Write to file instead of stdout"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing --out file"),
):
    """Export a session transcript as Markdown: sk export [SESSION] [--out f.md]"""
    from pathlib import Path

    from sk.store import export_session, latest_session, render_transcript

    name = session.strip() or latest_session()
    if not name:
        console.print("[red]no sessions yet — chat first, then export.[/red]")
        raise typer.Exit(1)
    events = export_session(name)
    if not events:
        console.print(f"[red]no such session '{name}' — see /sessions.[/red]")
        raise typer.Exit(1)
    text = render_transcript(name, events)
    if not out.strip():
        console.print(text)
        return
    dest = Path(out).expanduser()
    if dest.exists() and not force:
        console.print(f"[red]{dest} exists — pass --force to overwrite.[/red]")
        raise typer.Exit(1)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    except Exception as e:
        console.print(f"[red]cannot write {dest}: {e}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]exported {name} → {dest}[/green]")


@app.command()
def audit(
    session: str = typer.Option("", "--session", "-s", help="Session id (omit for all)"),
    format: str = typer.Option("md", "--format", "-f", help="md or json"),
    limit: int = typer.Option(200, "--limit", "-n", help="Rows to show"),
):
    """Compliance log: tool runs with approve/deny + local-vs-egress. Fully offline."""
    import datetime as _dt
    import json as _json

    from sk.store import is_local_traffic, list_tool_runs

    rows = list_tool_runs(session=session.strip(), limit=max(1, min(limit, 1000)))
    if format.strip().lower().startswith("json"):
        console.print(_json.dumps(rows, indent=2, default=str))
        return
    scope = f"session `{session}`" if session.strip() else "all sessions"
    if not rows:
        console.print(f"[dim](no tool runs logged for {scope} yet — run something first)[/dim]")
        return
    egress = sum(1 for r in rows if not is_local_traffic(r["provider"], r["host"]))
    console.print(
        f"[bold]audit[/] {scope} — {len(rows)} runs, "
        f"[green]{len(rows) - egress} local[/green] / "
        f"[yellow]{egress} egress[/yellow]"
    )
    for r in reversed(rows):
        ts = _dt.datetime.fromtimestamp(r["ts"]).strftime("%m-%d %H:%M")
        if not r["approved"]:
            mark = "[red]DENIED[/red]"
        elif not r["ok"]:
            mark = "[red]✗[/red]"
        else:
            mark = "[green]✓[/green]"
        where = (
            "[green]local[/green]"
            if is_local_traffic(r["provider"], r["host"])
            else (f"[yellow]→ {r['provider'] or '?'}@{r['host'] or '?'}[/yellow]")
        )
        console.print(
            f"[dim]{ts}[/dim] {mark} [cyan]{r['tool']}[/cyan] {r['target'][:100]}  {where}"
            + (f" [dim]({r['session']})[/dim]" if not session.strip() else "")
        )


@app.command()
def oops(
    model: str = typer.Option("", help="Model override or fast/smart"),
    no_stream: bool = typer.Option(False, "--no-stream", help="Disable streaming"),
):
    """Explain last failed command: sk oops"""
    from sk.store import last_failed

    fail = last_failed()
    if not fail:
        console.print("[green]No failures logged. Clean shell.[/green]")
        return
    _, cmd, cwd, rc = fail
    cfg = _cfg()
    cfg.model = _resolve_model(cfg, model)

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
            approve=_make_approver(True, cfg.approved_commands),
            auto_approve=True,
            session="oops",
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
    project: list[str] = typer.Option(
        [], "--project", "-p", help="Extra project path (repeatable)"
    ),
    smart: bool = typer.Option(False, "--smart", help="Pipe digest through LLM for 2-line summary"),
    model: str = typer.Option("", help="Model for --smart (or fast/smart)"),
):
    """Morning digest: system + git + memories. Instant, no LLM unless --smart."""
    from rich.table import Table

    from sk.brief import DEFAULT_PROJECTS, gather_brief

    _cfg()  # establish project namespace default for memories
    projs = DEFAULT_PROJECTS + list(project or [])
    data = gather_brief(projs)
    console.print(Panel(f"[bold]sidekick brief[/]  {data['when']}", expand=False))

    # system: trim sysinfo to essentials
    sysinfo: str = data["sysinfo"]
    # show RAM/GPU/disk lines only to stay short
    keep: list[str] = []
    for line in sysinfo.splitlines():
        ll = line.lower()
        if (
            line.startswith("CPU:")
            or line.startswith("GPU:")
            or "mem:" in ll
            or "memory" in ll
            or "/dev/nvme" in line
            or "OLLAMA MODELS" in line
            or "qwen" in line
            or "llama" in line
            or "NAME " in line
        ):
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
        console.print('[dim]no memories yet — sk remember "..."[/dim]')

    todos: list = data.get("todos", [])
    if todos:
        console.print(
            Panel(
                "\n".join(f"○ #{i} {t}" for i, t, _ in todos[:5]), title="open todos", expand=False
            )
        )

    if smart:
        cfg = _cfg()
        cfg.model = _resolve_model(cfg, model)
        from sk.agent import run_agent
        from sk.store import get_history

        digest = f"SYSTEM:\n{sysinfo[:1500]}\nPROJECTS:\n{data['projects']}\nMEMORIES:\n{mems[:5]}"
        console.print("[dim]summarizing...[/dim]")
        run_agent(
            f"Give a 3-bullet morning brief from this digest. Flag disk>90%, dirty git repos, and what to work on first.\n{digest[:4000]}",
            get_history("default")[-5:],
            cfg,
            on_tool=_make_on_tool(),
            on_token=_make_on_token(),
            approve=_make_approver(True, cfg.approved_commands),
            auto_approve=True,
            session="default",
        )
        console.print()
        console.print("[dim]--- done ---[/dim]")
