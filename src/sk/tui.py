"""Hermes-style single chat view. Everything via /commands — type /help."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, RichLog


def _w(log: RichLog, s: str, markup: bool = False) -> None:
    """Write to log. markup=True only for our own chrome (no user/model brackets)."""
    log.write(Text.from_markup(s) if markup else Text(s))


class SidekickTUI(App):
    TITLE = "sidekick"
    CSS = """
    RichLog { height: 1fr; border: solid #333; }
    Input { margin: 1 0; }
    """

    def __init__(self, model: str = ""):
        super().__init__()
        self.model_override = model
        self.state: dict = {"yolo": False}

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="chat-log", wrap=True, highlight=True)
        yield Input(placeholder="ask anything — /help for commands", id="chat-input")
        yield Footer()

    def on_mount(self) -> None:
        self._sub()
        self.query_one("#chat-log", RichLog).write("sidekick online. `/help` for commands, `/model fast` for speed.")

    def _sub(self) -> None:
        from .config import Config

        cfg = Config.load()
        model = self.model_override or cfg.model
        mode = "yolo" if self.state.get("yolo") else "confirm"
        self.sub_title = f"{model} · {mode} · /help"

    def _approve(self, name: str, args: dict) -> bool:
        from .tools import WRITE_TOOLS

        return True if name not in WRITE_TOOLS else bool(self.state.get("yolo"))

    @on(Input.Submitted, "#chat-input")
    def _send(self, ev: Input.Submitted) -> None:
        from . import slash

        text = ev.value.strip()
        if not text:
            return
        ev.input.clear()
        log = self.query_one("#chat-log", RichLog)
        _w(log, f"you> {text}")
        if text.startswith("/"):
            # /model switches session model (and saves default)
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
                _w(log, f"model → `{name}`")
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
                _w(log, out.text)
            self._sub()
            if out.agent_prompt:
                self.run_worker(self._answer(out.agent_prompt, show_as="oops"), exclusive=True)
            return
        self.run_worker(self._answer(text), exclusive=True)

    async def _answer(self, text: str, show_as: str = "") -> None:
        import asyncio

        from .agent import run_agent
        from .config import Config
        from .store import get_history, save_message

        log = self.query_one("#chat-log", RichLog)
        if not show_as:
            _w(log, "thinking...")
        cfg = Config.load()
        if self.model_override:
            cfg.model = self.model_override
        save_message("tui", "user", text)
        hist = get_history("tui")

        def on_tool(name: str, args: dict) -> None:
            preview = args if name not in ("write_file",) else {"path": args.get("path")}
            try:
                self.call_from_thread(_w, log, f"○ tool: {name} {preview}")
            except Exception:
                pass

        try:
            answer = await asyncio.to_thread(run_agent, text, hist, cfg, on_tool, None, self._approve)
        except Exception as e:
            try:
                self.call_from_thread(_w, log, f"Error: {e}")
            except Exception:
                _w(log, f"Error: {e}")
            return
        save_message("tui", "assistant", answer)
        try:
            self.call_from_thread(_w, log, f"sidekick> {answer or '(empty)'}")
        except Exception:
            _w(log, f"sidekick> {answer or '(empty)'}")


def launch(model: str = "") -> None:
    SidekickTUI(model=model).run()
