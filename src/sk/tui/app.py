"""TUI SidekickTUI application shell (split from sk/tui.py, pure move)."""

from __future__ import annotations

import time

from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Header, Label, ListItem, ListView, RichLog, Static, TextArea

from .helpers import _load_history, _now, _role, _rule, _w, is_affirmative, log_error
from .theme import install_sidekick_theme
from .widgets import ChatArea, ChatLog


class SidekickTUI(App):
    TITLE = "sidekick"
    BINDINGS = [
        ("ctrl+y", "copy_last", "copy last answer"),
        ("ctrl+g", "mic", "push to talk"),
        Binding("pageup", "scroll_log_up", "scroll up", priority=True),
        Binding("pagedown", "scroll_log_down", "scroll down", priority=True),
        ("ctrl+home", "scroll_log_top", "top"),
        ("ctrl+end", "scroll_log_bottom", "bottom"),
        ("f1", "toggle_help", "help"),
        ("f2", "toggle_theme", "theme"),
        ("f3", "toggle_sessions", "sessions"),
        ("escape", "close_help", "close"),
    ]
    CSS = """
    RichLog { height: 1fr; border: solid $primary-muted; }
    #live { height: auto; max-height: 10; border: solid $primary-muted; display: none; }
    #slash-list { height: auto; max-height: 8; border: solid $primary-muted; display: none; }
    #help-panel { height: auto; max-height: 14; border: solid $secondary; display: none; }
    #input-row { height: 5; }
    ChatArea { width: 1fr; height: 5; border: solid $primary-muted; }
    ChatArea:focus { border: solid $primary; }
    #mic-status { width: 22; height: 5; border: solid $primary-muted; color: $text-muted; content-align: center middle; }
    #mic-status.recording { border: solid $error; color: $error; }
    #status-bar { height: 1; color: $text-muted; background: $surface; }
    #sessions-drawer { dock: left; width: 44; height: 1fr; border: solid $primary-muted; background: $surface; display: none; }
    """

    def __init__(self, model: str = "", session: str = "", allow: tuple[str, ...] = ()):
        super().__init__()
        self.model_override = model
        from sk.store import new_session_id

        self.session = session or new_session_id("tui")
        self._continued = bool(session)
        self.state: dict = {"yolo": False, "allow": tuple(allow)}
        self._live_parts: list[str] = []
        self._live_reason: list[str] = []
        self._live_n: int = 0
        self._live_rendered_at: float = 0.0
        self._stats: str = ""
        self._pending_approval: dict[str, object] | None = None
        self._plan_approved: frozenset[str] | None = None
        self._drawer_sessions: list[str] = []
        self._slash_names: list[str] = []
        self._think_timer = None
        self._rec_proc = None
        self._rec_wav: str = ""
        self._rec_timer = None
        self._rec_start: float = 0.0
        self._transcribing: bool = False
        self.mic_state: str = "idle"  # idle | recording | busy (mirrors the pill)
        self._last_copied_selection: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield ListView(id="sessions-drawer")
        yield ChatLog(id="chat-log", wrap=True, highlight=True)
        yield RichLog(id="live", wrap=True, highlight=False)
        yield ListView(id="slash-list")
        yield RichLog(id="help-panel", wrap=True, highlight=False)
        with Horizontal(id="input-row"):
            yield ChatArea(id="chat-input", show_line_numbers=False)
            yield Static("ctrl+g\nto talk", id="mic-status")
        yield Static("", id="status-bar")

    def _help_text(self) -> str:
        from textual.binding import Binding

        from sk.slash import COMMANDS

        def entries(bindings) -> list[str]:
            out = []
            for b in bindings:
                if isinstance(b, tuple):
                    key, _action, desc = b
                elif isinstance(b, Binding):
                    key, desc = b.key, b.description
                else:  # pragma: no cover - defensive
                    continue
                if desc:
                    out.append(f"{key} {desc}")
            return out

        keys = entries(type(self).BINDINGS) + entries(ChatArea.BINDINGS)
        # NOTE: remove the migration block after one release cycle.
        migrated = (
            "Recently changed: scroll was ctrl+b/f → pageup/pagedown · talk was ctrl+t → ctrl+g"
        )
        cmds = [f"/{n} — {d}" for n, d in COMMANDS]
        return "KEYS\n" + "\n".join(keys) + "\n\n" + migrated + "\n\nCOMMANDS\n" + "\n".join(cmds)

    def action_toggle_help(self) -> None:
        try:
            panel = self.query_one("#help-panel", RichLog)
            if panel.display:
                panel.styles.display = "none"
                return
            panel.clear()
            panel.write(self._help_text())
            panel.styles.display = "block"
            panel.scroll_home(animate=False)
        except Exception:
            pass

    def action_toggle_theme(self) -> None:
        """F2: flip dark <-> light, persist to config. Existing log lines keep
        their baked-in colors; new output uses the new palette."""
        from sk.config import Config

        from .theme import mode_for_name, toggle_theme

        name = toggle_theme(self)
        try:
            cfg = Config.load()
            cfg.theme = mode_for_name(name)
            cfg.save()
        except Exception:
            pass
        try:
            self._sub()
        except Exception:
            pass

    def action_close_help(self) -> None:
        if self.close_help_if_open():
            return
        self._hide_sessions_drawer()

    def _sessions_visible(self) -> bool:
        try:
            return bool(self.query_one("#sessions-drawer", ListView).display)
        except Exception:
            return False

    def _refresh_sessions(self) -> None:
        from sk.store import list_sessions

        try:
            lst = self.query_one("#sessions-drawer", ListView)
        except Exception:
            return
        try:
            rows = list_sessions(limit=20)
        except Exception:
            rows = []
        self._drawer_sessions = [r["session"] for r in rows]
        lst.clear()
        for r in rows:
            mark = "● " if r["session"] == self.session else "○ "
            lst.append(ListItem(Label(f"{mark}{r['preview'][:32]} ({r['count']})")))

    def action_toggle_sessions(self) -> None:
        try:
            lst = self.query_one("#sessions-drawer", ListView)
        except Exception:
            return
        if lst.display:
            self._hide_sessions_drawer()
            return
        self._refresh_sessions()
        lst.styles.display = "block"
        try:
            lst.focus()
        except Exception:
            pass

    def _hide_sessions_drawer(self) -> None:
        try:
            self.query_one("#sessions-drawer", ListView).styles.display = "none"
        except Exception:
            pass
        try:
            self.query_one("#chat-input", ChatArea).focus()
        except Exception:
            pass

    @on(ListView.Selected, "#sessions-drawer")
    def _sessions_chosen(self, ev: ListView.Selected) -> None:
        try:
            idx = ev.list_view.index if ev.list_view.index is not None else 0
            session_id = self._drawer_sessions[idx]
        except Exception:
            return
        self._hide_sessions_drawer()
        self._show_session(session_id, "")

    def _show_session(self, session_id: str, notice: str = "") -> None:
        """Adopt a session + render its tail. Shared by /resume and the drawer."""
        from sk.store import get_history as _gh

        log = self.query_one("#chat-log", RichLog)
        self.session = session_id
        self._sub()
        log.clear()
        _role(log, "sys", f"now on `{self.session}`")
        for m in _gh(self.session)[-10:]:
            role = "you" if m["role"] == "user" else "sidekick"
            if role == "sidekick":
                _role(log, role, "")
                try:
                    from rich.markdown import Markdown

                    log.write(Markdown(m["content"][:1500]))
                except Exception:
                    _role(log, role, m["content"][:1500])
            else:
                _role(log, role, m["content"][:1500])
        if notice:
            _role(log, "", notice)

    def close_help_if_open(self) -> bool:
        """Hide the help panel if visible. Returns True when it did."""
        try:
            panel = self.query_one("#help-panel", RichLog)
            if panel.display:
                panel.styles.display = "none"
                return True
        except Exception:
            pass
        return False

    # ---- slash autocomplete ----
    def _slash_items(self, fragment: str) -> list[tuple[str, str]]:
        from sk.slash import COMMANDS

        frag = fragment.lower()
        starts = [(n, d) for n, d in COMMANDS if n.split()[0].lower().startswith(frag)]
        contains = [
            (n, d)
            for n, d in COMMANDS
            if frag and frag not in n.split()[0].lower() and frag in n.lower()
        ]
        return (starts + contains)[:12]

    def slash_update(self, text: str) -> None:
        """Refresh/hide the suggestion list from current input. Returns nothing."""
        from sk.slash import COMMANDS

        try:
            lst = self.query_one("#slash-list", ListView)
        except Exception:
            return
        first = text.strip().split("\n")[0] if text else ""
        if not first.startswith("/"):
            lst.styles.display = "none"
            return
        token = first[1:].split()[0] if len(first) > 1 else ""
        items = [(n, d) for n, d in COMMANDS[:12]] if not token else self._slash_items(token)
        self._slash_names = [n for n, _ in items]
        lst.clear()
        for name, desc in items:
            lst.append(ListItem(Label(f"/{name} — {desc}")))
        lst.styles.display = "block" if items else "none"
        if items:
            try:
                lst.index = 0
            except Exception:
                pass

    def slash_visible(self) -> bool:
        try:
            lst = self.query_one("#slash-list", ListView)
            return lst.display and bool(len(lst))
        except Exception:
            return False

    def slash_complete_active(self, text: str = "") -> bool:
        """True when Enter should complete instead of send: list visible with
        a highlighted item that differs from what's already typed."""
        try:
            lst = self.query_one("#slash-list", ListView)
            if not (lst.display and len(lst) and lst.highlighted_child is not None):
                return False
            names = getattr(self, "_slash_names", [])
            idx = lst.index if lst.index is not None else 0
            typed = (text or "").strip().split()
            typed_cmd = typed[0][1:] if typed and typed[0].startswith("/") else ""
            return bool(names) and names[min(idx, len(names) - 1)].split()[0] != typed_cmd
        except Exception:
            return False

    def slash_complete(self) -> None:
        try:
            names = getattr(self, "_slash_names", [])
            lst = self.query_one("#slash-list", ListView)
            area = self.query_one("#chat-input", ChatArea)
            idx = lst.index if lst.index is not None else 0
            if not names:
                return
            name = names[min(idx, len(names) - 1)].split()[0]
            rest = area.text.split(None, 1)
            area.text = f"/{name} " + (rest[1] if len(rest) > 1 else "")
            area.cursor_to_end()
        except Exception:
            pass
        finally:
            self.slash_dismiss()

    def slash_dismiss(self) -> None:
        try:
            self.query_one("#slash-list", ListView).styles.display = "none"
        except Exception:
            pass

    def slash_navigate(self, step: int) -> bool:
        """Move highlight if list visible. Returns True when consumed."""
        try:
            lst = self.query_one("#slash-list", ListView)
        except Exception:
            return False
        if not lst.display or not len(lst):
            return False
        try:
            idx = lst.index if lst.index is not None else 0
            lst.index = max(0, min(len(lst) - 1, idx + step))
        except Exception:
            pass
        return True

    @on(TextArea.Changed, "#chat-input")
    def _slash_changed(self, ev: TextArea.Changed) -> None:
        self.slash_update(ev.text_area.text)

    def on_mount(self) -> None:
        install_sidekick_theme(self)
        area = self.query_one("#chat-input", ChatArea)
        area.cmd_history = _load_history()
        area.focus()
        self._sub()
        log = self.query_one("#chat-log", RichLog)
        _w(
            log,
            "sidekick online. Enter sends · ctrl+j newline · ↑ history · ctrl+g to talk · pgup/pgdn scroll · drag to select (auto-copies on release), `ctrl+y` copies selection (else last answer).",
        )
        if self._continued:
            from sk.store import get_history

            _role(log, "sys", f"continued `{self.session}`")
            for m in get_history(self.session)[-10:]:
                if m["role"] == "user":
                    _role(log, "you", m["content"][:1500])
                else:
                    _role(log, "sidekick", "")
                    try:
                        from rich.markdown import Markdown

                        log.write(Markdown(m["content"][:1500]))
                    except Exception:
                        _role(log, "sidekick", m["content"][:1500])
        try:
            from sk.cli import _code_version

            _w(log, f"build {_code_version()} (`sk version` to compare after updates)")
        except Exception:
            pass
        if self._is_fresh():
            _role(
                log,
                "",
                "New here? Try: `what files are in ~/` · `/model fast` for speed · `/help` for everything.",
            )

    @staticmethod
    def _is_fresh() -> bool:
        try:
            from sk.store import list_sessions

            return not list_sessions(limit=1)
        except Exception:
            return False

    def action_mic(self) -> None:
        self._mic_toggle()

    def _mic_status(self, text: str, recording: bool = False, state: str = "idle") -> None:
        """Mic status pill (display-only; ctrl+g is the trigger)."""
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
            {
                "up": log.scroll_page_up,
                "down": log.scroll_page_down,
                "top": log.scroll_home,
                "bottom": log.scroll_end,
            }[what]()
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

        from sk import voice as _voice

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
            _role(log, "", "recording... press ctrl+g to stop")
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
        from sk import voice as _voice

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
            self._mic_status("ctrl+g\nto talk")
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

        from sk import voice as _voice

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
        self._mic_status("ctrl+g\nto talk")
        _role(self.query_one("#chat-log", RichLog), "error", msg)

    def _drop_transcript(self, text: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        self._mic_status("ctrl+g\nto talk")
        area = self.query_one("#chat-input", ChatArea)
        cur = area.text.strip()
        area.text = (cur + " " + text).strip() if cur else text
        area.cursor_to_end()
        area.focus()
        _role(log, "", f"heard> {text[:200]} (edit + Enter to send)")

    def _sub(self) -> None:
        from sk.config import Config

        from .theme import name_for_mode, set_theme

        cfg = Config.load()
        self._cfg = cfg
        set_theme(self, name_for_mode(cfg.theme))
        model = self.model_override or cfg.model
        mode = "yolo" if self.state.get("yolo") else "confirm"
        tail = f" · {self._stats}" if self._stats else ""
        prov = f"{cfg.provider} · " if cfg.provider not in ("ollama", "") else ""
        short = self.session[-13:] if len(self.session) > 16 else self.session
        self.sub_title = f"{prov}{model} · {mode} · {short} · /help{tail}"
        self._render_status_bar()

    def _render_status_bar(self) -> None:
        """Mirror sub_title into the bottom status strip. Best effort."""
        try:
            self.query_one("#status-bar", Static).update(self.sub_title)
        except Exception:
            pass

    def _approve(self, name: str, args: dict) -> bool:
        """Approval gate for worker threads. Reads auto-pass; writes either
        auto-pass (/yolo) or block on an inline [y/N] question answered by
        the user's next input line (timeout denies, and says so)."""
        from sk.tools import approval_tools

        if name not in approval_tools():
            return True
        if bool(self.state.get("yolo")):
            return True
        cfg = getattr(self, "_cfg", None)
        if cfg is not None:
            from sk.config import is_project_approved, is_session_allowed

            if is_project_approved(name, args, cfg.approved_commands):
                return True
            if is_session_allowed(name, args, tuple(self.state.get("allow", ()) or ())):
                return True
        plan = getattr(self, "_plan_approved", None)
        if plan:
            from sk.agent import _tool_target

            if _tool_target(name, args) in plan:
                return True
        path = args.get("path", args.get("cmd", "?"))
        preview = str(args.get("content", ""))[:200] if name == "write_file" else ""
        if name == "edit_file":
            preview = f"old: {str(args.get('old_string', ''))[:120]}"
        if name == "shell":
            preview = f"$ {str(args.get('cmd', ''))[:200]}"
        if name == "delete_file":
            preview = "(PERMANENT delete)"
        return self._wait_slot(name, path, preview)

    def _wait_slot(self, name: str, path: str, preview: str, timeout: float = 300) -> bool:
        """Post an inline [y/N] slot, wait for the answer, clean up. Shared by
        per-tool approval and plan review so timeout/stale semantics match."""
        import threading
        import time as _t

        timeout = float(getattr(self, "_approve_timeout", timeout))
        event = threading.Event()
        token = object()
        owner = threading.get_ident()
        deadline = _t.monotonic() + timeout + 30
        asked_at = _t.monotonic()
        self._pending_approval = {
            "question": f"{name} -> {path}",
            "event": event,
            "answer": False,
            "asked_at": asked_at,
            "reply": "",
            "token": token,
            "owner": owner,
            "deadline": deadline,
        }

        def _log_outcome(result: str) -> None:
            try:
                import datetime as _dt

                from sk.config import CONFIG_DIR

                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                with open(CONFIG_DIR / "tui-errors.log", "a") as f:
                    f.write(
                        f"[{_dt.datetime.now():%Y-%m-%d %H:%M:%S}] approve: {name} -> {path} = {result} ({_t.monotonic() - asked_at:.0f}s)\n"
                    )
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
            slot = getattr(self, "_pending_approval", None)
            if slot is not None and slot.get("token") is token:
                pending, self._pending_approval = slot, None
            else:
                pending = None
        if expired:
            _log_outcome("timeout-denied")
            try:
                self.call_from_thread(
                    _role,
                    self.query_one("#chat-log", RichLog),
                    "warn",
                    f"no answer in {int(timeout)}s — denied (reply faster, or /yolo)",
                )
            except Exception:
                pass
            return False
        if pending is None:
            _log_outcome("slot-stolen-denied")
            return False  # slot stolen/cleared concurrently: fail closed
        _log_outcome(
            "approved"
            if pending.get("answer")
            else f"denied reply={pending.get('reply', '')[:20]!r}"
        )
        if not bool(pending.get("answer", False)):
            try:
                self.call_from_thread(
                    _role,
                    self.query_one("#chat-log", RichLog),
                    "sys",
                    f"denied (you answered '{pending.get('reply', '')[:20]}')",
                )
            except Exception:
                pass
        return bool(pending.get("answer", False))

    def _review_plan(self, plan_text: str, calls: list) -> bool:
        """One confirmation for a whole multi-tool plan. Plan-approved targets
        auto-pass their per-tool prompts for the rest of this turn."""
        if bool(self.state.get("yolo")):
            return True
        from sk.agent import _tool_target

        approved = frozenset({_tool_target(n, a) for n, a in calls})
        ok = self._wait_slot("plan", f"{len(calls)} tools", plan_text)
        self._plan_approved = approved if ok else None
        return ok

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
        from rich.panel import Panel as _Panel
        from rich.text import Text as _Text

        from .theme import active_roles

        roles = active_roles()
        warn_color = roles.get("warn", "bold yellow").split()[-1]
        log = self.query_one("#chat-log", RichLog)
        log.write(
            _Panel(
                _Text(preview if preview else "(no preview)"),
                title=f"allow {name} -> {path}? \\[y/N]",
                subtitle=f"y or --yes approves · {timeout}s (timeout denies)",
                border_style=warn_color,
            )
        )
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
            self._clear_log_selection()
            return
        if selected == getattr(self, "_last_copied_selection", None):
            self._clear_log_selection()
            return
        self._last_copied_selection = selected
        if self._copy_out(selected, "selection"):
            self._clear_log_selection()

    def _clear_log_selection(self) -> None:
        """Drop the log's screen selection so the drag highlight disappears."""
        log = self.query_one("#chat-log", RichLog)
        self.screen.selections = {w: s for w, s in self.screen.selections.items() if w is not log}

    def _copy_out(self, text: str, what: str) -> bool:
        """Copy text. Returns True on success; confirms via a toast popup."""
        from sk.clip import backends_available, copy_text, install_hint

        if backends_available():
            try:
                method = copy_text(text)
            except Exception as e:
                _role(self.query_one("#chat-log", RichLog), "error", f"copy failed ({e})")
                return False
            self.notify(f"Copied {what} via {method}", title="Copied")
            return True
        try:
            self.copy_to_clipboard(text)  # driver-safe OSC52
        except Exception as e:
            _role(
                self.query_one("#chat-log", RichLog),
                "error",
                f"copy failed ({e}) — {install_hint()}",
            )
            return False
        hint = install_hint()
        message = "Sent via terminal clipboard" + (f" — {hint}" if hint else "")
        self.notify(message, title="Copied via OSC52", severity="warning")
        return True

    def action_copy_last(self) -> None:
        from sk.store import get_history

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
        answers = [m["content"] for m in get_history(self.session) if m["role"] == "assistant"]
        if not answers:
            _w(log, f"[{_now()}] (no answers to copy yet)")
            return
        self._copy_out(answers[-1], "last answer")

    @on(ChatArea.Send)
    def _send(self, ev: ChatArea.Send) -> None:
        from sk import slash

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
            verdict = is_affirmative(text)
            _role(log, "you", text)
            pending["answer"] = verdict
            pending["reply"] = text[:20]
            _role(
                log, "sys", f"{'approved' if verdict else 'denied'}: {pending.get('question', '')}"
            )
            try:
                pending["event"].set()
            except Exception:
                pass
            return
        _role(log, "you", text)
        _rule(log)
        if text.startswith("/"):
            if text.startswith("/model ") and text[7:].strip():
                from sk.config import Config
                from sk.slash import _resolve_model_name

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
            from sk.config import Config

            cfg = Config.load()
            if self.model_override:
                cfg.model = self.model_override
            out = slash.handle(text, session=self.session, cfg=cfg, state=self.state)
            if out.quit:
                self.exit()
                return
            if out.switch_session:
                self._show_session(out.switch_session, out.text)
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
        self._think_dots = 0
        self._turn_start = time.monotonic()
        self._live_rendered_at = 0.0
        try:
            live = self.query_one("#live", RichLog)
            live.styles.display = "block"
            live.clear()
            live.write("· thinking")
        except Exception:
            pass
        try:
            if self._think_timer is not None:
                self._think_timer.stop()
        except Exception:
            pass
        try:
            self._think_timer = self.set_interval(0.4, self._think_tick)
        except Exception:
            self._think_timer = None

    def _think_tick(self) -> None:
        if self._live_n > 0:
            self._stop_think_timer()
            return
        try:
            self._think_dots = (self._think_dots + 1) % 4
            live = self.query_one("#live", RichLog)
            live.clear()
            live.write("· thinking" + "." * self._think_dots)
        except Exception:
            pass

    def _stop_think_timer(self) -> None:
        try:
            if self._think_timer is not None:
                self._think_timer.stop()
        except Exception:
            pass
        self._think_timer = None

    def _push_live(self) -> None:
        """Progressive Markdown render, throttled to ~2Hz. Raw tokens accumulate
        silently between renders; unclosed fences render as code until closed."""
        try:
            now = time.monotonic()
            if self._live_rendered_at and now - self._live_rendered_at < 0.5:
                return
            self._live_rendered_at = now
            live = self.query_one("#live", RichLog)
            live.clear()
            head = "".join(self._live_reason)[-500:]
            if head:
                live.write(Text("…" + head, style="dim"))
                live.write(Text("───", style="dim"))
            body = "".join(self._live_parts)[-2000:]
            try:
                live.write(Markdown(body))
            except Exception:
                live.write(Text(body))
        except Exception:
            pass

    async def _answer(self, text: str, show_as: str = "") -> None:
        import asyncio

        from sk.agent import run_agent
        from sk.config import Config
        from sk.store import get_history, save_message

        log = self.query_one("#chat-log", RichLog)
        cfg = Config.load()
        if self.model_override:
            cfg.model = self.model_override
        save_message(self.session, "user", text)
        hist = get_history(self.session)

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
            from sk.agent import audit_session

            token = audit_session.set(self.session)
            try:
                answer = await asyncio.to_thread(
                    run_agent,
                    text,
                    hist,
                    cfg,
                    on_tool,
                    None,
                    self._approve,
                    on_reasoning,
                    bool(self.state.get("yolo")),
                    review_plan=self._review_plan,
                )
            finally:
                audit_session.reset(token)
                self._plan_approved = None  # turn-scoped: never leak into next turn
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
        save_message(self.session, "assistant", answer)
        try:
            self.call_from_thread(self._finish, answer or "(empty)", f"{secs:.0f}s · ~{toks}tok")
        except Exception:
            self._finish(answer or "(empty)", f"{secs:.0f}s · ~{toks}tok")

    def _hide_live(self) -> None:
        self._live_parts = []
        self._live_reason = []
        self._live_rendered_at = 0.0
        self._stop_think_timer()
        try:
            live = self.query_one("#live", RichLog)
            live.clear()
            live.styles.display = "none"
        except Exception:
            pass

    def _finish(self, answer: str, stats: str) -> None:

        self._hide_live()
        self._stats = stats
        self._sub()
        log = self.query_one("#chat-log", RichLog)
        _role(log, "sidekick", "")
        try:
            log.write(Markdown(answer))
        except Exception:
            _role(log, "sidekick", answer)
        _rule(log)
