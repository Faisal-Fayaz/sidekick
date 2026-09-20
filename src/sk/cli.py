"""CLI: sk chat / sk run / sk models / sk doctor / sk config"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

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
    from .tools import APPROVAL_TOOLS

    def approve(name: str, args: dict) -> bool:
        if name not in APPROVAL_TOOLS:
            return True
        target = args.get("path", args.get("cmd", "?"))
        preview = ""
        if name == "write_file":
            c = str(args.get("content", ""))
            preview = c[:600] + ("... [truncated]" if len(c) > 600 else "")
        elif name == "make_dir":
            preview = "(new directory)"
        elif name == "shell":
            preview = f"$ {str(args.get('cmd', ''))[:600]}"
        elif name == "delete_file":
            preview = "(PERMANENT delete)"
        else:
            old = str(args.get("old_string", ""))[:300]
            new = str(args.get("new_string", ""))[:300]
            preview = f"OLD:\n{old}\nNEW:\n{new}"
        console.print(Panel(f"[bold yellow]approval[/] {name} -> [cyan]{target}[/cyan]\n{preview}", expand=False))
        if auto_yes:
            console.print("[dim]--yes: auto-approved[/dim]")
            return True
        try:
            return typer.confirm("Allow?", default=False)
        except (EOFError, KeyboardInterrupt):
            return False

    return approve


def _make_approver_state(state: dict):
    """Like _make_approver but reads live state['yolo'] (for /yolo toggling)."""

    def approve(name: str, args: dict) -> bool:
        from .tools import APPROVAL_TOOLS

        if name not in APPROVAL_TOOLS:
            return True
        if state.get("yolo"):
            console.print(f"[dim]yolo: auto-approved {name} -> {args.get('path', '?')}[/dim]")
            return True
        return _make_approver(False)(name, args)

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


def _resolve_model(cfg, model_opt: str, task: str = "") -> str:
    """--model > SIDEKICK_MODEL > config. Supports fast/smart/auto aliases.

    fast/smart resolve per provider (ollama: llama3.2:3b / qwen2.5-coder:7b).
    auto = router picks a tier from task text (sk run default).
    """
    if model_opt:
        from .config import provider_tier

        m = model_opt.strip()
        if m == "fast":
            return provider_tier(cfg.provider, "fast", cfg.model)
        if m == "smart":
            return provider_tier(cfg.provider, "smart", cfg.model)
        if m == "auto":
            from .router import FAST_MODEL, SMART_MODEL, pick_model

            picked, reason = pick_model(task, FAST_MODEL)
            if cfg.provider in ("ollama", "lmstudio", "custom"):
                console.print(f"[dim]router → {picked} ({reason})[/dim]")
                return picked
            tier = "smart" if picked == SMART_MODEL else "fast"
            resolved = provider_tier(cfg.provider, tier, cfg.model)
            console.print(f"[dim]router → {resolved} ({reason})[/dim]")
            return resolved
        return m
    return cfg.model


@app.command()
def chat(
    session: str = typer.Option("default", help="Session name for history"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve writes"),
    model: str = typer.Option("", help="Model override: name or fast/smart"),
    no_stream: bool = typer.Option(False, "--no-stream", help="Disable live token streaming"),
):
    """Interactive REPL: sk chat — try /help"""
    cfg = _cfg()
    cfg.model = _resolve_model(cfg, model)
    state = {"yolo": yes}
    console.print(Panel(f"[bold]sidekick[/]  model=[cyan]{cfg.model}[/]  session=[cyan]{session}[/]\nType [bold]/help[/] for commands, [bold]@path[/] to attach a file.", expand=False))
    approve = _make_approver_state(state)
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
        if user.lower() in {"exit", "quit", ":q"} and not user.startswith("/"):
            console.print("bye.")
            break

        if user.startswith("/"):
            from . import slash as _slash

            out = _slash.handle(user, session=session, cfg=cfg, state=state)
            if out.quit:
                console.print("bye.")
                break
            if out.clear_view:
                console.print("[dim]--- session cleared ---[/dim]")
            if out.text:
                console.print(Markdown(out.text))
            if out.agent_prompt:
                user = out.agent_prompt
                console.print(f"[dim]oops → {out.agent_prompt[:80]}...[/dim]")
            else:
                continue

        history = get_history(session)
        save_message(session, "user", user)

        console.print("[dim]thinking... (streams live)[/dim]")
        try:
            answer = run_agent(user, history, cfg, on_tool=on_tool, on_token=on_token, approve=approve, auto_approve=bool(state.get("yolo")))
        except Exception as e:
            console.print(f"[red]Error talking to {cfg.provider} ({cfg.effective_base_url()} model={cfg.model}): {e}[/red]")
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
def talk(
    session: str = typer.Option("voice", help="Session name for history"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve writes"),
    model: str = typer.Option("", help="Model override: name or fast/smart"),
    stt_model: str = typer.Option("tiny", help="faster-whisper size: tiny/base/small"),
    duration: int = typer.Option(0, "--duration", "-d", help="Fixed record seconds (0 = Enter to start/stop)"),
    install: bool = typer.Option(False, "--install", help="Install faster-whisper without asking"),
    device: str = typer.Option("default", help="ALSA device, e.g. hw:2,0"),
):
    """Push-to-talk voice chat. All transcription happens on your CPU."""
    import tempfile
    import time as _t

    from . import voice as _voice

    cfg = _cfg()
    cfg.model = _resolve_model(cfg, model)
    ok, msg = _voice.check_mic()
    if not ok:
        console.print(f"[red]{msg}[/red]")
        raise typer.Exit(1)
    ok, msg = _voice.ensure_stt()
    if not ok:
        console.print(f"[yellow]{msg}[/yellow]")
        if not install and not typer.confirm("Install now?", default=True):
            raise typer.Exit(1)
        console.print("[dim]installing faster-whisper into sidekick's env (one time, ~800MB)...[/dim]")
        ok, out = _voice.install_stt()
        if not ok:
            console.print(f"[red]{out}[/red]")
            raise typer.Exit(1)
        console.print("[dim]installed.[/dim]")
    state = {"yolo": yes}
    console.print(Panel(f"[bold]sidekick talk[/]  model=[cyan]{cfg.model}[/]  stt=[cyan]{stt_model}[/] (local int8)\n[bold green]Enter[/] to record, [bold green]Enter[/] to stop. [bold]/quit[/] exits, [bold]/help[/] commands.", expand=False))
    approve = _make_approver_state(state)
    on_tool = _make_on_tool()
    on_token = _make_on_token()
    import tempfile

    while True:
        try:
            first = console.input("[bold green]talk> [/]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nbye.")
            break
        if first.lower() in {"/quit", "/exit", ":q"}:
            console.print("bye.")
            break
        if first.startswith("/") and first.lower() not in ("/quit",):
            from . import slash as _slash

            out = _slash.handle(first, session=session, cfg=cfg, state=state)
            if out.quit:
                console.print("bye.")
                break
            if out.text:
                console.print(Markdown(out.text))
            if not out.agent_prompt:
                continue
            user = out.agent_prompt
        else:
            out_wav = f"{tempfile.mkdtemp(prefix='sk-voice-')}/in.wav"
            if duration > 0:
                console.print(f"[red]● REC {duration}s...[/red]")
                try:
                    out_wav = str(_voice.record_once(duration, device))
                except Exception as e:
                    console.print(f"[red]record failed: {e}[/red]")
                    continue
            else:
                console.print("[red]● REC — Enter to stop...[/red]")
                proc = _voice.start_recording(out_wav, device)
                try:
                    console.input("")
                except (EOFError, KeyboardInterrupt):
                    console.print("\nbye.")
                    _voice.stop_recording(proc, wav_path=out_wav)
                    break
                err = _voice.stop_recording(proc, wav_path=out_wav)
                if err:
                    console.print(f"[red]{err}[/red]")
                    continue
            console.print("[dim]transcribing locally...[/dim]")
            try:
                user = _voice.transcribe(out_wav, stt_model)
            except Exception as e:
                console.print(f"[red]{e}[/red]")
                continue
            console.print(f"[bold green]heard> [/]{user}")
        history = get_history(session)
        save_message(session, "user", user)
        console.print("[dim]thinking... (streams live)[/dim]")
        try:
            t0 = _t.monotonic()
            answer = run_agent(user, history, cfg, on_tool=on_tool, on_token=on_token, approve=approve, auto_approve=bool(state.get("yolo")))
            console.print(f"[dim]({ _t.monotonic() - t0:.0f}s)[/dim]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            continue
        save_message(session, "assistant", answer)
        console.print()
        streamed = getattr(on_token, "state", {}).get("n", 0) if on_token else 0
        if streamed < len(answer or "") * 0.5:
            console.print(Markdown(answer or "(empty)"))
        console.print("[dim]--- done ---[/dim]")
        console.print()


@app.command(name="mic-test")
def mic_test(
    duration: int = typer.Option(3, "--duration", "-d", help="Record seconds"),
    device: str = typer.Option("default", help="ALSA device, e.g. hw:2,0"),
):
    """Check mic levels: records, measures peak/RMS, tells you what to fix."""
    from . import voice as _voice

    console.print(f"[dim]recording {duration}s — speak normally...[/dim]")
    try:
        res = _voice.mic_level(duration, device)
    except Exception as e:
        console.print(f"[red]mic test failed: {e}[/red]")
        raise typer.Exit(1)
    color = "green" if res.get("verdict") == "good" else ("yellow" if res.get("ok") else "red")
    console.print(f"[{color}]mic: {res.get('verdict')} ({res.get('peak_db', '?')} dB peak)[/]")
    console.print(f"[dim]{res.get('hint', '')}[/dim]")


def _code_version() -> str:
    """Short git hash of the running checkout (dev) or package version."""
    try:
        import subprocess as _sp

        r = _sp.run(["git", "-C", str(Path(__file__).resolve().parent.parent.parent), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        h = (r.stdout or "").strip()
        if h:
            return h
    except Exception:
        pass
    try:
        from . import __version__

        return __version__
    except Exception:
        return "unknown"


@app.command()
def version():
    """Show running code version (git hash). Compare with TUI header."""
    console.print(f"sk {_code_version()}")


@app.command()
def run(
    task: str = typer.Argument(..., help="Task in quotes, e.g. \"summarize disk usage\""),
    session: str = typer.Option("default", help="Session name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve writes (else prompts)"),
    model: str = typer.Option("auto", help="Model: auto (router), fast, smart, or name"),
    no_stream: bool = typer.Option(False, "--no-stream", help="Disable live token streaming"),
):
    """Single-shot: sk run \"summarize disk usage in ~/\" """
    cfg = _cfg()
    cfg.model = _resolve_model(cfg, model, task)
    history = get_history(session)
    mode = "auto-approve writes" if yes else "confirm writes"
    console.print(f"[dim]task: {task}  model: {cfg.model} ({mode})[/dim]")
    save_message(session, "user", task)

    approve = _make_approver(yes)
    on_tool = _make_on_tool()
    on_token = None if no_stream else _make_on_token()

    console.print("[dim]working... (streams live)[/dim]")
    try:
        answer = run_agent(task, history, cfg, on_tool=on_tool, on_token=on_token, approve=approve, auto_approve=yes)
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
    """List models for the current provider."""
    from .auth import fetch_models

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
    from .auth import provider_status

    cfg = _cfg()
    console.print(f"provider=[cyan]{cfg.provider}[/cyan] model=[cyan]{cfg.model}[/cyan] base=[cyan]{cfg.effective_base_url()}[/cyan] key=[cyan]{Config.mask(cfg.effective_api_key())}[/cyan] code=[cyan]{_code_version()}[/cyan]")
    ok, msg = provider_status(cfg)
    if ok and cfg.provider in ("ollama", "lmstudio"):
        from .auth import fetch_models

        try:
            names = fetch_models(cfg.provider, cfg.effective_base_url(), cfg.effective_api_key())
            console.print(f"[green]✓ {cfg.provider} reachable[/green] ({len(names)} models)")
            if cfg.model in names:
                console.print(f"[green]✓ model '{cfg.model}' installed[/green]")
            else:
                console.print(f"[yellow]! model '{cfg.model}' not found. Run: ollama pull {cfg.model}[/yellow]")
        except Exception as e:
            console.print(f"[red]✗ {cfg.provider} not reachable: {e}[/red]")
    elif ok:
        console.print(f"[green]✓ {cfg.provider} reachable[/green] ({msg})")
    else:
        console.print(f"[red]✗ {msg}[/red]")
        if cfg.provider == "ollama":
            console.print("[dim]Run `ollama serve` in another terminal.[/dim]")
    # quick tool sanity
    from .tools import tool_exec, tool_list_dir

    console.print(f"[dim]tools sanity: {tool_exec('pwd')[:80]}[/dim]")


@app.command()
def config(
    model: str = typer.Option("", help="Set model, e.g. --model qwen3:4b"),
    provider: str = typer.Option("", help="Set provider: ollama|openai|groq|together|deepseek|openrouter|google|lmstudio|custom"),
    api_key: str = typer.Option("", help="Set API key (or use SIDEKICK_API_KEY env)"),
    base_url: str = typer.Option("", help="Custom base URL (sets provider=custom unless --provider given)"),
    show: bool = typer.Option(False, "--show", help="Show current config (key masked)"),
):
    """View/set config. Keys are chmod-600’d; env vars always win."""
    from .config import PRESETS

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
        console.print(f"provider={cfg.provider}\nmodel={cfg.model}\nbase_url={cfg.effective_base_url()}\napi_key={Config.mask(cfg.effective_api_key())}\nmax_steps={cfg.max_steps}\ntemp={cfg.temperature}")


auth_app = typer.Typer(help="Keys: sk auth add/list/status/remove (keys masked, validated live)")
app.add_typer(auth_app, name="auth")


def _pick_provider(default: str = "") -> str:
    from .config import PRESETS

    names = list(PRESETS)
    if default and default in names:
        return default
    console.print("Provider:")
    for i, n in enumerate(names, 1):
        console.print(f"  {i}. {n}")
    while True:
        try:
            raw = console.input("Pick [1-{}] ({}): ".format(len(names), default or "ollama")).strip() or default or "ollama"
        except (EOFError, KeyboardInterrupt):
            raise typer.Exit(1)
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return names[int(raw) - 1]
        if raw.lower() in names:
            return raw.lower()
        console.print("[red]not in list, try again[/red]")


def _ask_key() -> str:
    try:
        return (typer.prompt("API key (hidden)", hide_input=True) or "").strip()
    except (EOFError, KeyboardInterrupt):
        raise typer.Exit(1)


@auth_app.command("add")
def auth_add(
    provider: str = typer.Argument("", help="Provider, omit for picker"),
    key: str = typer.Option("", help="Key inline (hidden prompt if omitted)"),
):
    """Add a key: sk auth add groq (validates live before saving)."""
    from .auth import validate_key
    from .config import PRESETS

    cfg = _cfg()
    p = provider.strip().lower() or _pick_provider(cfg.provider)
    if p not in PRESETS:
        console.print(f"[red]unknown provider. Pick: {', '.join(PRESETS)}[/red]")
        raise typer.Exit(1)
    base = PRESETS[p]["base_url"] if p != cfg.provider or not cfg.base_url else cfg.effective_base_url()
    k = key.strip() or _ask_key()
    if p in ("ollama", "lmstudio"):
        cfg.provider, cfg.model, cfg.base_url, cfg.api_key = p, PRESETS[p]["model"] or cfg.model, "", ""
        cfg.save()
        console.print(f"[green]provider set to {p}, no key needed locally.[/green]")
        return
    ok, msg = validate_key(p, base, k)
    console.print(f"[green]✓ {msg}[/green]" if ok else f"[red]✗ {msg}[/red]")
    if not ok:
        raise typer.Exit(1)
    cfg.provider, cfg.base_url, cfg.api_key = p, "", k
    if not cfg.model or cfg.model in (PRESETS.get(cfg.provider, {}).get("model", ""),):
        cfg.model = PRESETS[p]["model"]
    cfg.save()
    console.print(f"[green]saved. Model is {cfg.model} — change with `sk model`.[/green]")


@auth_app.command("list")
def auth_list():
    """Show providers + masked key state."""
    from .config import PRESETS

    cfg = _cfg()
    for n in PRESETS:
        cur = "← current" if n == cfg.provider else ""
        key = Config.mask(cfg.effective_api_key()) if n == cfg.provider else "—"
        console.print(f"• [cyan]{n}[/cyan] key={key} {cur}")


@auth_app.command("status")
def auth_status(provider: str = typer.Argument("", help="Provider, omit for current")):
    """Validate reachability + key for a provider."""
    from .auth import provider_status

    cfg = _cfg()
    if provider.strip():
        from .config import PRESETS

        p = provider.strip().lower()
        if p not in PRESETS:
            console.print(f"[red]unknown provider[/red]")
            raise typer.Exit(1)
        import copy

        cfg = copy.copy(cfg)
        cfg.provider = p
        if p != _cfg().provider:
            cfg.base_url, cfg.api_key = "", ""
    ok, msg = provider_status(cfg)
    console.print(f"[green]✓ {cfg.provider}: {msg}[/green]" if ok else f"[red]✗ {cfg.provider}: {msg}[/red]")
    if not ok:
        raise typer.Exit(1)


@auth_app.command("remove")
def auth_remove(provider: str = typer.Argument("", help="Provider, omit for current")):
    """Forget a key (and reset model default)."""
    from .config import PRESETS

    cfg = _cfg()
    p = (provider.strip().lower() or cfg.provider)
    if p not in PRESETS:
        console.print("[red]unknown provider[/red]")
        raise typer.Exit(1)
    if p == cfg.provider:
        cfg.api_key, cfg.base_url, cfg.model = "", "", PRESETS[p]["model"]
        cfg.save()
    console.print(f"[yellow]forgot {p}.[/yellow]")


@app.command()
def model():
    """Interactive picker: provider → live model list → default."""
    from .auth import fetch_models
    from .config import PRESETS

    cfg = _cfg()
    p = _pick_provider(cfg.provider)
    try:
        names = fetch_models(p, PRESETS[p]["base_url"], cfg.effective_api_key() if p == cfg.provider else "")
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
    if not names:
        console.print("[yellow]empty list.[/yellow]")
        raise typer.Exit(1)
    console.print(f"Models on {p}:")
    for i, n in enumerate(names[:30], 1):
        console.print(f"  {i}. {n}{' ← current' if n == cfg.model and p == cfg.provider else ''}")
    while True:
        try:
            raw = console.input(f"Pick [1-{min(len(names), 30)}] or id: ").strip()
        except (EOFError, KeyboardInterrupt):
            raise typer.Exit(1)
        pick = names[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= min(len(names), 30) else raw
        if pick:
            break
        console.print("[red]empty, try again[/red]")
    cfg.provider, cfg.model = p, pick
    if p != "custom":
        cfg.base_url = ""
    cfg.save()
    console.print(f"[green]default → {p} / {pick}[/green]")


@app.command()
def setup():
    """Guided setup: provider → key → validate → model → hook → test run."""
    from .auth import provider_status

    console.print(Panel("[bold]sidekick setup[/] — provider, key, model, hook, test.", expand=False))
    cfg = _cfg()
    p = _pick_provider(cfg.provider)
    from .config import PRESETS

    cfg.provider = p
    if p in ("ollama", "lmstudio"):
        cfg.model, cfg.base_url, cfg.api_key = PRESETS[p]["model"] or cfg.model, "", ""
        cfg.save()
        console.print(f"[green]local provider {p}, no key needed.[/green]")
    else:
        k = _ask_key()
        ok, msg = provider_status(_cfg_with(cfg, api_key=k))
        console.print(f"[green]✓ {msg}[/green]" if ok else f"[red]✗ {msg}[/red]")
        if not ok:
            raise typer.Exit(1)
        cfg.api_key, cfg.base_url = k, ""
        cfg.save()
        console.print("[green]key saved (chmod 600).[/green]")
    console.print("[dim]now pick a model...[/dim]")
    model()
    cfg = _cfg()
    try:
        if typer.confirm("Install shell hook (logs commands for history/oops)?", default=False):
            hook_install(shell="", write=True)
    except (EOFError, KeyboardInterrupt):
        pass
    console.print("[dim]test run...[/dim]")
    try:
        answer = run_agent("say hi in 5 words", [], _cfg(), approve=_make_approver(True), auto_approve=True)
        console.print(Markdown((answer or "")[:500]))
    except Exception as e:
        console.print(f"[red]test run failed: {e}[/red]")
        raise typer.Exit(1)
    console.print("[green]setup complete. Try `sk tui`.[/green]")


def _cfg_with(cfg, api_key: str):
    import copy

    c = copy.copy(cfg)
    c.api_key = api_key
    return c


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
            auto_approve=True,
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
            auto_approve=True,
        )
        console.print()
        console.print("[dim]--- done ---[/dim]")


@app.command()
def tui(
    model: str = typer.Option("", help="Model override or fast/smart"),
    mouse: str = typer.Option("", "--mouse", help="on|off, omit for saved preference"),
):
    """Fullscreen chat. Mouse on: click + wheel; hold Shift to select text."""
    from .tui import launch

    cfg = _cfg()
    m: bool | None = None
    if mouse.strip().lower() in ("on", "1", "true", "yes"):
        m = True
    elif mouse.strip().lower() in ("off", "0", "false", "no"):
        m = False
    launch(_resolve_model(cfg, model), mouse=m)


@app.command()
def skills():
    """List skill packs in ~/.sidekick/skills/ (auto-loaded into prompt)."""
    from .skills import SKILLS_DIR, list_skills, load_skills

    rows = list_skills()
    console.print(f"[dim]{SKILLS_DIR} — {len(rows)} packs[/dim]")
    for name, size in rows:
        console.print(f"• [cyan]{name}[/cyan] ({size}b)")
    console.print("[dim]Add your own: echo '# my skill\\n- rule' > ~/.sidekick/skills/my.md[/dim]")


@app.command(name="skills-install")
def skills_install(
    name: str = typer.Argument("superpowers", help="Preset (superpowers) or git URL"),
    force: bool = typer.Option(False, "--force", help="Re-clone if present"),
):
    """Install skill packs: sk skills-install superpowers"""
    from .skills import install_preset

    console.print(f"[dim]installing {name}...[/dim]")
    out = install_preset(name, force=force)
    if out.startswith("Installed"):
        console.print(f"[green]{out}[/green]")
    else:
        console.print(f"[yellow]{out}[/yellow]")


@app.command()
def daemon(
    once: bool = typer.Option(False, "--once", help="Single check, then exit"),
    interval: int = typer.Option(300, "--interval", help="Seconds between checks in loop mode"),
    disk_warn: int = typer.Option(90, "--disk-warn", help="Disk % threshold"),
):
    """Watcher: disk + shell failures + dirty repos. Loop foreground; use --once for cron."""
    import time

    from .daemon import append_log, check_once, load_state, save_state

    def run_one() -> int:
        nudges, state = check_once(load_state(), disk_warn=disk_warn)
        save_state(state)
        if nudges:
            for n in nudges:
                console.print(f"[yellow]! {n}[/yellow]")
            append_log(nudges)
        else:
            console.print("[green]clean — no nudges.[/green]")
        return len(nudges)

    if once:
        run_one()
        return
    console.print(f"[dim]daemon loop every {interval}s (Ctrl-C to stop). Log: ~/.sidekick/nudges.log[/dim]")
    try:
        while True:
            run_one()
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\nstopped.")


if __name__ == "__main__":
    app()
