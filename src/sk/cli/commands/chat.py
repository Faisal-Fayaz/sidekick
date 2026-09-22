"""CLI commands: interactive chat, voice, TUI entry (split from sk/cli.py, pure move)."""

from __future__ import annotations

import typer
from rich.markdown import Markdown
from rich.panel import Panel

from sk.agent import run_agent
from sk.store import get_history, save_message

from ..approvers import _make_approver_state, _make_on_token, _make_on_tool
from ..base import _cfg, app, console
from ..resolve import _resolve_model


@app.command()
def chat(
    session: str = typer.Option("", help="Session name (omit for fresh, --continue for latest)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve writes"),
    model: str = typer.Option("", help="Model override: name or fast/smart"),
    no_stream: bool = typer.Option(False, "--no-stream", help="Disable live token streaming"),
    cont: bool = typer.Option(False, "--continue", help="Resume the latest session"),
):
    """Interactive REPL: sk chat — try /help"""
    from sk.store import latest_session, new_session_id

    cfg = _cfg()
    cfg.model = _resolve_model(cfg, model)
    if cont:
        session = latest_session() or new_session_id("chat")
    elif not session:
        session = new_session_id("chat")
    state = {"yolo": yes}
    console.print(
        Panel(
            f"[bold]sidekick[/]  model=[cyan]{cfg.model}[/]  session=[cyan]{session}[/]\nType [bold]/help[/] for commands, [bold]@path[/] to attach a file.",
            expand=False,
        )
    )
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
            from sk import slash as _slash

            out = _slash.handle(user, session=session, cfg=cfg, state=state)
            if out.quit:
                console.print("bye.")
                break
            if out.switch_session:
                session = out.switch_session
                console.print(f"[dim]--- now on {session} ---[/dim]")
                for m in get_history(session)[-10:]:
                    who = "[bold green]you> [/]" if m["role"] == "user" else "sidekick> "
                    console.print(f"{who}{m['content'][:300]}")
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
            answer = run_agent(
                user,
                history,
                cfg,
                on_tool=on_tool,
                on_token=on_token,
                approve=approve,
                auto_approve=bool(state.get("yolo")),
                session=session,
            )
        except Exception as e:
            console.print(
                f"[red]Error talking to {cfg.provider} ({cfg.effective_base_url()} model={cfg.model}): {e}[/red]"
            )
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
    duration: int = typer.Option(
        0, "--duration", "-d", help="Fixed record seconds (0 = Enter to start/stop)"
    ),
    install: bool = typer.Option(False, "--install", help="Install faster-whisper without asking"),
    device: str = typer.Option("default", help="ALSA device, e.g. hw:2,0"),
):
    """Push-to-talk voice chat. All transcription happens on your CPU."""
    import tempfile
    import time as _t

    from sk import voice as _voice

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
        console.print(
            "[dim]installing faster-whisper into sidekick's env (one time, ~800MB)...[/dim]"
        )
        ok, install_msg = _voice.install_stt()
        if not ok:
            console.print(f"[red]{install_msg}[/red]")
            raise typer.Exit(1)
        console.print("[dim]installed.[/dim]")
    state = {"yolo": yes}
    console.print(
        Panel(
            f"[bold]sidekick talk[/]  model=[cyan]{cfg.model}[/]  stt=[cyan]{stt_model}[/] (local int8)\n[bold green]Enter[/] to record, [bold green]Enter[/] to stop. [bold]/quit[/] exits, [bold]/help[/] commands.",
            expand=False,
        )
    )
    approve = _make_approver_state(state)
    on_tool = _make_on_tool()
    on_token = _make_on_token()

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
            from sk import slash as _slash

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
            answer = run_agent(
                user,
                history,
                cfg,
                on_tool=on_tool,
                on_token=on_token,
                approve=approve,
                auto_approve=bool(state.get("yolo")),
                session=session,
            )
            console.print(f"[dim]({_t.monotonic() - t0:.0f}s)[/dim]")
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
    from sk import voice as _voice

    console.print(f"[dim]recording {duration}s — speak normally...[/dim]")
    try:
        res = _voice.mic_level(duration, device)
    except Exception as e:
        console.print(f"[red]mic test failed: {e}[/red]")
        raise typer.Exit(1)
    color = "green" if res.get("verdict") == "good" else ("yellow" if res.get("ok") else "red")
    console.print(f"[{color}]mic: {res.get('verdict')} ({res.get('peak_db', '?')} dB peak)[/]")
    console.print(f"[dim]{res.get('hint', '')}[/dim]")


@app.command()
def tui(
    model: str = typer.Option("", help="Model override or fast/smart"),
    cont: bool = typer.Option(False, "--continue", help="Resume the latest session"),
):
    """Fullscreen chat (fresh session each launch unless --continue)."""
    from sk.tui import launch

    cfg = _cfg()
    launch(_resolve_model(cfg, model), cont=cont)
