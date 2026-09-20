"""Hermes-style single chat view. Everything via /commands — type /help.

Enter sends, ctrl+j newline, up/down history, ctrl+y copies last answer.
"""

from __future__ import annotations

import time

from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Footer, Header, RichLog, Static, TextArea
from textual.containers import Horizontal


def _w(log: RichLog, s: str, markup: bool = False) -> None:
    """Write to log. markup=True only for our own chrome (no user/model brackets)."""
    log.write(Text.from_markup(s) if markup else Text(s))


def log_error(where: str, exc: BaseException) -> None:
    """Persist TUI errors where the user can't copy them. Best effort."""
    import traceback

    from .config import CONFIG_DIR

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_DIR / "tui-errors.log", "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {where}: {exc!r}\n")
            f.write(traceback.format_exc()[-2000:] + "\n")
    except Exception:
        pass


ROLE_STYLES = {
    "you": "bold green",
    "sidekick": "bold cyan",
    "tool": "dim",
    "sys": "dim",
    "warn": "bold yellow",
    "error": "bold red",
}


def _line(when: str, role: str, body: str) -> Text:
    """Role-colored chat line: dim timestamp, colored `role>`, neutral body.

    Bodies stay neutral on purpose — brackets and code copy cleanly and the
    role color alone carries who-is-who.
    """
    t = Text()
    t.append(f"[{when}] ", style="dim")
    if role:
        t.append(f"{role}> ", style=ROLE_STYLES.get(role, ""))
    t.append(body)
    return t


def _role(log: RichLog, role: str, body: str) -> None:
    log.write(_line(_now(), role, body))


def _now() -> str:
    return time.strftime("%H:%M")


def _load_history() -> list[str]:
    import json

    from .config import CONFIG_DIR

    try:
        items = json.loads((CONFIG_DIR / "input_history").read_text())
        return [str(x) for x in items if str(x).strip()][:100]
    except Exception:
        return []


def _save_history(items: list[str]) -> None:
    import json

    from .config import CONFIG_DIR

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        (CONFIG_DIR / "input_history").write_text(json.dumps(items[-100:]))
    except Exception:
        pass


class ChatLog(RichLog):
    """Chat history with working mouse-drag text selection.

    Stock RichLog renders RichVisual, which the base get_selection() can't
    extract text from — so drag-selection in it copies nothing. We extract
    from the stored line texts instead.
    """

    ALLOW_SELECT = True

    def get_selection(self, selection) -> tuple[str, str] | None:
        try:
            from textual.selection import Selection as _Sel

            text = "\n".join(ln.text for ln in self.lines)
            if not text.strip():
                return None
            extracted = selection.extract(text) if hasattr(selection, "extract") else ""
            return (extracted, "\n") if extracted else None
        except Exception:
            return None

    def on_mouse_up(self, event) -> None:
        if getattr(event, "button", 1) != 1:
            return
        app = self.app
        if hasattr(app, "_copy_selection_if_any"):
            try:
                app.call_after_refresh(app._copy_selection_if_any)
            except Exception:
                app._copy_selection_if_any()


class ChatArea(TextArea):
    """Multiline input: Enter sends, ctrl+j / alt+enter newline, up/down history."""

    BINDINGS = [
        Binding("enter", "send", "send", priority=True, show=False),
        Binding("ctrl+j", "newline", "newline", show=False),
        Binding("alt+enter", "newline", "newline", show=False),
        Binding("ctrl+y", "copy_last", "copy last answer", priority=True, show=False),
        Binding("up", "hist_prev", "history", show=False),
        Binding("down", "hist_next", "history", show=False),
    ]

    class Send(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cmd_history: list[str] = []
        self.hist_idx: int = -1  # -1 = not browsing

    def action_send(self) -> None:
        text = self.text.strip()
        if text:
            self.hist_idx = -1
            self.post_message(ChatArea.Send(text))
        self.clear()

    def action_copy_last(self) -> None:
        # TextArea binds ctrl+y to redo; this priority binding reclaims it.
        self.app.action_copy_last()

    def action_newline(self) -> None:
        self.insert("\n")

    def push_history(self, text: str) -> None:
        if text and (not self.cmd_history or self.cmd_history[-1] != text):
            self.cmd_history.append(text)
            _save_history(self.cmd_history)

    def cursor_to_end(self) -> None:
        lines = self.text.split("\n")
        end = (len(lines) - 1, len(lines[-1]))
        try:
            self.selection = type(self.selection)(end, end)
        except Exception:
            pass

    def _browse(self, step: int) -> None:
        if "\n" in self.text or not self.cmd_history:
            if step < 0:
                self.action_cursor_up()
            else:
                self.action_cursor_down()
            return
        if self.hist_idx == -1:
            self.hist_idx = len(self.cmd_history) if step < 0 else -1
        self.hist_idx = max(-1, min(len(self.cmd_history) - 1, self.hist_idx + step))
        self.text = self.cmd_history[self.hist_idx] if self.hist_idx >= 0 else ""
        self.cursor_to_end()

    def action_hist_prev(self) -> None:
        self._browse(-1)

    def action_hist_next(self) -> None:
        self._browse(1)


class SidekickTUI(App):
    TITLE = "sidekick"
    BINDINGS = [
        ("ctrl+y", "copy_last", "copy last answer"),
        ("ctrl+t", "mic", "push to talk"),
        ("ctrl+b", "scroll_log_up", "scroll up"),
        ("ctrl+f", "scroll_log_down", "scroll down"),
        ("ctrl+home", "scroll_log_top", "top"),
        ("ctrl+end", "scroll_log_bottom", "bottom"),
    ]
    CSS = """
    RichLog { height: 1fr; border: solid #1d3327; }
    #live { height: auto; max-height: 10; border: solid #1d3327; display: none; }
    #input-row { height: 5; }
    ChatArea { width: 1fr; height: 5; border: solid #1d3327; }
    ChatArea:focus { border: solid #00ff9d; }
    #mic-status { width: 22; height: 5; border: solid #1d3327; color: #9b9bab; content-align: center middle; }
    #mic-status.recording { border: solid #ff5555; color: #ff5555; }
    """

    def __init__(self, model: str = ""):
        super().__init__()
        self.model_override = model
        self.state: dict = {"yolo": False}
        self._live_parts: list[str] = []
        self._live_reason: list[str] = []
        self._live_n: int = 0
        self._stats: str = ""
        self._pending_approval = None
        self._rec_proc = None
        self._rec_wav: str = ""
        self._rec_timer = None
        self._rec_start: float = 0.0
        self._transcribing: bool = False
        self.mic_state: str = "idle"  # idle | recording | busy (mirrors the pill)
        self._last_copied_selection: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield ChatLog(id="chat-log", wrap=True, highlight=True)
        yield TextArea(id="live", read_only=True, show_line_numbers=False)
        with Horizontal(id="input-row"):
            yield ChatArea(id="chat-input", show_line_numbers=False)
            yield Static("ctrl+t\nto talk", id="mic-status")
        yield Footer()

    def on_mount(self) -> None:
        try:
            from textual.theme import Theme

            self.register_theme(Theme(name="sidekick", primary="#00ff9d", secondary="#7c3aed", accent="#ffb000", background="#0b0f0c", surface="#111613", panel="#111613"))
            self.theme = "sidekick"
        except Exception:
            pass
        area = self.query_one("#chat-input", ChatArea)
        area.cmd_history = _load_history()
        area.focus()
        self._sub()
        log = self.query_one("#chat-log", RichLog)
        _w(log, "sidekick online. Enter sends · ctrl+j newline · ↑ history · ctrl+t to talk · ctrl+b/f scroll · drag to select (auto-copies on release), `ctrl+y` copies selection (else last answer).")
        try:
            from .cli import _code_version

            _w(log, f"build {_code_version()} (`sk version` to compare after updates)")
        except Exception:
            pass

    def action_mic(self) -> None:
        self._mic_toggle()

    def _mic_status(self, text: str, recording: bool = False, state: str = "idle") -> None:
        """Mic status pill (display-only; ctrl+t is the trigger)."""
        self.mic_state = state
        try:
            pill = self.query_one("#mic-status", Static)
            pill.update(text)
            if recording:
                pill.add_class("recording")
            else:
                pill.remove_class("recording")
        except Exception:
            pass

    def _scroll_log(self, what: str) -> None:
        # mouse tracking stays off (native copy), so the log scrolls by key.
        # TextArea never sees these keys (unbound there) — they reach the app.
        log = self.query_one("#chat-log", RichLog)
        try:
            {"up": log.scroll_page_up, "down": log.scroll_page_down, "top": log.scroll_home, "bottom": log.scroll_end}[what]()
        except Exception:
            pass

    def action_scroll_log_up(self) -> None:
        self._scroll_log("up")

    def action_scroll_log_down(self) -> None:
        self._scroll_log("down")

    def action_scroll_log_top(self) -> None:
        self._scroll_log("top")

    def action_scroll_log_bottom(self) -> None:
        self._scroll_log("bottom")

    def _mic_toggle(self) -> None:
        import time as _t

        from . import voice as _voice

        log = self.query_one("#chat-log", RichLog)
        try:
            self._mic_toggle_inner(log, _voice, _t)
        except Exception as e:
            log_error("mic-toggle", e)
            _role(log, "error", f"mic crashed: {e} (logged to ~/.sidekick/tui-errors.log)")

    def _mic_toggle_inner(self, log: RichLog, _voice, _t) -> None:
        if self._transcribing:
            _w(log, f"[{_now()}] still transcribing, hold on...")
            return
        if self._rec_proc is None:
            ok, msg = _voice.check_mic()
            if not ok:
                _role(log, "error", msg)
                return
            ok, msg = _voice.ensure_stt()
            if not ok:
                _role(log, "warn", f"{msg} — `sk talk --install` in a shell, then retry")
                return
            import tempfile

            self._rec_wav = f"{tempfile.mkdtemp(prefix='sk-voice-')}/in.wav"
            try:
                self._rec_proc = _voice.start_recording(self._rec_wav)
            except Exception as e:
                _role(log, "error", f"mic failed: {e}")
                self._rec_proc = None
                return
            self._rec_start = _t.monotonic()
            self._mic_status("■ REC\n0s", recording=True, state="recording")
            self._rec_timer = self.set_interval(1.0, self._rec_tick)
            _role(log, "", "recording... press ctrl+t to stop")
        else:
            self._mic_stop()

    def _rec_tick(self) -> None:
        import time as _t

        try:
            secs = int(_t.monotonic() - self._rec_start)
            self._mic_status(f"■ REC\n{secs}s", recording=True, state="recording")
        except Exception:
            pass

    def _mic_stop(self) -> None:
        from . import voice as _voice

        log = self.query_one("#chat-log", RichLog)
        proc, self._rec_proc = self._rec_proc, None
        if self._rec_timer is not None:
            try:
                self._rec_timer.stop()
            except Exception:
                pass
            self._rec_timer = None
        self._mic_status("…busy…", state="busy")
        if proc is None:
            self._mic_status("ctrl+t\nto talk")
            return
        err = _voice.stop_recording(proc, wav_path=self._rec_wav)
        if err:
            _role(log, "error", err)
            return
        self._transcribing = True
        _role(log, "", "transcribing locally...")
        self.run_worker(self._do_transcribe(self._rec_wav), exclusive=True)

    async def _do_transcribe(self, wav: str) -> None:
        import asyncio

        from . import voice as _voice

        log = self.query_one("#chat-log", RichLog)
        try:
            text = await asyncio.to_thread(_voice.transcribe, wav)
        except Exception as e:
            self._transcribing = False
            try:
                self.call_from_thread(self._mic_failed, str(e))
            except Exception:
                self._mic_failed(str(e))
            return
        finally:
            import shutil

            shutil.rmtree(wav.rsplit("/", 1)[0], ignore_errors=True)
        self._transcribing = False
        try:
            self.call_from_thread(self._drop_transcript, text)
        except Exception:
            self._drop_transcript(text)

    def _mic_failed(self, msg: str) -> None:
        self._mic_status("ctrl+t\nto talk")
        _role(self.query_one("#chat-log", RichLog), "error", msg)

    def _drop_transcript(self, text: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        self._mic_status("ctrl+t\nto talk")
        area = self.query_one("#chat-input", ChatArea)
        cur = area.text.strip()
        area.text = (cur + " " + text).strip() if cur else text
        area.cursor_to_end()
        area.focus()
        _role(log, "", f"heard> {text[:200]} (edit + Enter to send)")

    def _sub(self) -> None:
        from .config import Config

        cfg = Config.load()
        model = self.model_override or cfg.model
        mode = "yolo" if self.state.get("yolo") else "confirm"
        tail = f" · {self._stats}" if self._stats else ""
        self.sub_title = f"{model} · {mode} · /help{tail}"

    def _approve(self, name: str, args: dict) -> bool:
        """Approval gate for worker threads. Reads auto-pass; writes either
        auto-pass (/yolo) or block on an inline [y/N] question answered by
        the user's next input line (timeout denies, and says so)."""
        import threading
        import time as _t

        from .tools import APPROVAL_TOOLS

        if name not in APPROVAL_TOOLS:
            return True
        if bool(self.state.get("yolo")):
            return True
        path = args.get("path", args.get("cmd", "?"))
        preview = str(args.get("content", ""))[:200] if name == "write_file" else ""
        if name == "edit_file":
            preview = f"old: {str(args.get('old_string', ''))[:120]}"
        if name == "shell":
            preview = f"$ {str(args.get('cmd', ''))[:200]}"
        if name == "delete_file":
            preview = "(PERMANENT delete)"
        timeout = float(getattr(self, "_approve_timeout", 300))
        event = threading.Event()
        token = object()
        owner = threading.get_ident()
        deadline = _t.monotonic() + timeout + 30
        asked_at = _t.monotonic()
        self._pending_approval = {"question": f"{name} -> {path}", "event": event, "answer": False, "asked_at": asked_at, "reply": "", "token": token, "owner": owner, "deadline": deadline}

        def _log_outcome(result: str) -> None:
            try:
                import datetime as _dt

                from .config import CONFIG_DIR

                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                with open(CONFIG_DIR / "tui-errors.log", "a") as f:
                    f.write(f"[{_dt.datetime.now():%Y-%m-%d %H:%M:%S}] approve: {name} -> {path} = {result} ({_t.monotonic() - asked_at:.0f}s)\n")
            except Exception:
                pass

        try:
            self.call_from_thread(self._ask_approval, name, path, preview, int(timeout))
        except Exception:
            self._ask_approval(name, path, preview, int(timeout))
        try:
            expired = not event.wait(timeout=timeout)
        finally:
            # never leave a stale slot: only clear if still ours
            if getattr(self, "_pending_approval", None) is not None and self._pending_approval.get("token") is token:
                pending, self._pending_approval = self._pending_approval, None
            else:
                pending = None
        if expired:
            _log_outcome("timeout-denied")
            try:
                self.call_from_thread(_role, self.query_one("#chat-log", RichLog), "warn", f"no answer in {int(timeout)}s — denied (reply faster, or /yolo)")
            except Exception:
                pass
            return False
        if pending is None:
            _log_outcome("slot-stolen-denied")
            return False  # slot stolen/cleared concurrently: fail closed
        _log_outcome("approved" if pending.get("answer") else f"denied reply={pending.get('reply', '')[:20]!r}")
        if not bool(pending.get("answer", False)):
            try:
                self.call_from_thread(_role, self.query_one("#chat-log", RichLog), "sys", f"denied (you answered '{pending.get('reply', '')[:20]}')")
            except Exception:
                pass
        return bool(pending.get("answer", False))

    def _live_pending(self):
        """Active approval or None. Clears stale slots (dead owner, past deadline)."""
        import threading
        import time as _t

        pending = getattr(self, "_pending_approval", None)
        if pending is None:
            return None
        alive = any(t.ident == pending.get("owner") for t in threading.enumerate())
        if not alive or _t.monotonic() > pending.get("deadline", 0):
            self._pending_approval = None
            return None
        return pending

    def _ask_approval(self, name: str, path: str, preview: str, timeout: int = 300) -> None:
        log = self.query_one("#chat-log", RichLog)
        _role(log, "warn", f"allow {name} -> {path}? [y/N] (y or --yes approves, {timeout}s)")
        if preview:
            _w(log, f"  {preview}")
        try:
            self.query_one("#chat-input", ChatArea).focus()
        except Exception:
            pass

    def _selected_text(self) -> str:
        """Mouse-dragged text in the log, if any. Empty when nothing selected."""
        try:
            return (self.screen.get_selected_text() or "").strip()
        except Exception:
            return ""

    def _copy_selection_if_any(self) -> None:
        selected = self._selected_text()
        if not selected:
            self._last_copied_selection = ""
            return
        if selected == getattr(self, "_last_copied_selection", None):
            return
        self._last_copied_selection = selected
        self._copy_out(selected, "selection")

    def _copy_out(self, text: str, what: str) -> None:
        """Copy via reliable backends, OSC52 fallback with honest warning."""
        from .clip import backends_available, copy_text, install_hint

        log = self.query_one("#chat-log", RichLog)
        if backends_available():
            try:
                method = copy_text(text)
                _w(log, f"[{_now()}] copied {what} via {method}")
            except Exception as e:
                _role(log, "error", f"copy failed ({e})")
            return
        try:
            self.copy_to_clipboard(text)  # driver-safe OSC52
        except Exception as e:
            _role(log, "error", f"copy failed ({e}) — {install_hint()}")
            return
        _role(log, "warn", f"sent via terminal clipboard — {install_hint()} if paste comes up empty")

    def action_copy_last(self) -> None:
        from .store import get_history

        log = self.query_one("#chat-log", RichLog)
        # Hermes order: composer (input) selection first, then chat selection.
        try:
            drafted = (self.query_one("#chat-input", ChatArea).selected_text or "").strip()
        except Exception:
            drafted = ""
        if drafted:
            self._copy_out(drafted, "draft selection")
            return
        selected = self._selected_text()
        if selected:
            self._copy_out(selected, "selection")
            return
        answers = [m["content"] for m in get_history("tui") if m["role"] == "assistant"]
        if not answers:
            _w(log, f"[{_now()}] (no answers to copy yet)")
            return
        self._copy_out(answers[-1], "last answer")

    @on(ChatArea.Send)
    def _send(self, ev: ChatArea.Send) -> None:
        from . import slash

        text = ev.text.strip()
        if not text:
            return
        area = self.query_one("#chat-input", ChatArea)
        area.push_history(text)
        area.hist_idx = -1
        log = self.query_one("#chat-log", RichLog)
        if text.lower() in ("exit", "quit", ":q", "/exit", "/quit", "/q"):
            pending = self._live_pending()
            if pending is not None:
                try:
                    pending["event"].set()  # release worker; deny by default
                except Exception:
                    pass
                self._pending_approval = None
            _role(log, "sys", "bye.")
            self.exit()
            return
        # pending write approval eats the next NON-SLASH line: y/yes approves
        pending = self._live_pending()
        if pending is not None and not text.startswith("/"):
            verdict = text.lower() in ("y", "yes", "yup", "ok", "okay", "sure", "approve", "--yes", "-y")
            _role(log, "you", text)
            pending["answer"] = verdict
            pending["reply"] = text[:20]
            _role(log, "sys", f"{'approved' if verdict else 'denied'}: {pending.get('question', '')}")
            try:
                pending["event"].set()
            except Exception:
                pass
            return
        _role(log, "you", text)
        if text.startswith("/"):
            if text.startswith("/model ") and text[7:].strip():
                from .config import Config
                from .slash import _resolve_model_name

                name = _resolve_model_name(Config.load(), text[7:].strip())
                self.model_override = name
                cfg = Config.load()
                cfg.model = name
                try:
                    cfg.save()
                except Exception:
                    pass
                _role(log, "sys", f"model → `{name}`")
                self._sub()
                return
            from .config import Config

            cfg = Config.load()
            if self.model_override:
                cfg.model = self.model_override
            out = slash.handle(text, session="tui", cfg=cfg, state=self.state)
            if out.quit:
                self.exit()
                return
            if out.clear_view:
                log.clear()
            if out.text:
                _role(log, "", out.text)
            self._sub()
            if out.agent_prompt:
                self._prime_live()
                self.run_worker(self._answer(out.agent_prompt, show_as="oops"), exclusive=True)
            return
        self._prime_live()
        self.run_worker(self._answer(text), exclusive=True)

    def _prime_live(self) -> None:
        self._live_parts = []
        self._live_reason = []
        self._live_n = 0
        self._turn_start = time.monotonic()
        try:
            self.query_one("#live", TextArea).styles.display = "block"
        except Exception:
            pass

    def _push_live(self) -> None:
        try:
            live = self.query_one("#live", TextArea)
            body = "".join(self._live_parts)
            head = "".join(self._live_reason)[-500:]
            live.text = (("…" + head + "\n───\n") if head else "") + body[-2000:]
            try:
                lines = live.text.split("\n")
                end = (len(lines) - 1, len(lines[-1]))
                live.selection = type(live.selection)(end, end)
            except Exception:
                pass
            live.scroll_end(animate=False)
        except Exception:
            pass

    async def _answer(self, text: str, show_as: str = "") -> None:
        import asyncio

        from .agent import run_agent
        from .config import Config
        from .store import get_history, save_message

        log = self.query_one("#chat-log", RichLog)
        if not show_as:
            _role(log, "", "thinking...")
        cfg = Config.load()
        if self.model_override:
            cfg.model = self.model_override
        save_message("tui", "user", text)
        hist = get_history("tui")

        def on_tool(name: str, args: dict) -> None:
            preview = args if name not in ("write_file",) else {"path": args.get("path")}
            try:
                self.call_from_thread(_role, log, "tool", f"○ tool: {name} {preview}")
            except Exception:
                pass

        def on_token(tok: str) -> None:
            self._live_parts.append(tok)
            self._live_n += 1
            try:
                self.call_from_thread(self._push_live)
            except Exception:
                pass

        def on_reasoning(chunk: str) -> None:
            self._live_reason.append(chunk)
            try:
                self.call_from_thread(self._push_live)
            except Exception:
                pass

        try:
            answer = await asyncio.to_thread(
                run_agent, text, hist, cfg, on_tool, None, self._approve, on_reasoning, bool(self.state.get("yolo"))
            )
        except Exception as e:
            log_error("answer", e)
            try:
                self.call_from_thread(_role, log, "error", f"Error: {e}")
            except Exception:
                _role(log, "error", f"Error: {e}")
            try:
                self.call_from_thread(self._hide_live)
            except Exception:
                pass
            return
        secs = time.monotonic() - self._turn_start
        toks = max(1, len("".join(self._live_parts)) // 4)
        save_message("tui", "assistant", answer)
        try:
            self.call_from_thread(self._finish, answer or "(empty)", f"{secs:.0f}s · ~{toks}tok")
        except Exception:
            self._finish(answer or "(empty)", f"{secs:.0f}s · ~{toks}tok")

    def _hide_live(self) -> None:
        self._live_parts = []
        self._live_reason = []
        try:
            live = self.query_one("#live", TextArea)
            live.clear()
            live.styles.display = "none"
        except Exception:
            pass

    def _finish(self, answer: str, stats: str) -> None:
        from rich.markdown import Markdown

        self._hide_live()
        self._stats = stats
        self._sub()
        log = self.query_one("#chat-log", RichLog)
        _role(log, "sidekick", "")
        try:
            log.write(Markdown(answer))
        except Exception:
            _role(log, "sidekick", answer)


def launch(model: str = "") -> None:
    # Mouse tracking on: drag-select in the log auto-copies on release,
    # clicks and wheel work like every other TUI. Hold Shift to select
    # natively at terminal level.
    SidekickTUI(model=model).run(mouse=True)
