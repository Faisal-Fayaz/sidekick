"""Textual TUI: Brief | Todos | Memories | Chat in one terminal UI."""

from __future__ import annotations

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, TabbedContent, TabPane


class SidekickTUI(App):
    TITLE = "sidekick"
    SUB_TITLE = "local terminal companion"
    CSS = """
    DataTable { height: 1fr; }
    RichLog { height: 1fr; border: solid #333; }
    Input { margin: 1 0; }
    """

    def __init__(self, model: str = ""):
        super().__init__()
        self.model_override = model

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="brief"):
            with TabPane("Brief", id="brief"):
                yield Button("Refresh brief", id="brief-refresh", variant="primary")
                yield RichLog(id="brief-log", wrap=True, highlight=True)
            with TabPane("Todos", id="todos"):
                yield DataTable(id="todo-table")
                with Horizontal():
                    yield Input(placeholder="new todo... (Enter to add)", id="todo-input")
                    yield Button("Done #", id="todo-done", variant="warning")
                    yield Button("Clear done", id="todo-clear")
            with TabPane("Memories", id="memories"):
                yield DataTable(id="mem-table")
                with Horizontal():
                    yield Input(placeholder="remember... (Enter to save)", id="mem-input")
                    yield Button("Forget", id="mem-forget", variant="error")
            with TabPane("Chat", id="chat"):
                yield RichLog(id="chat-log", wrap=True, highlight=True)
                yield Input(placeholder="ask sidekick... (Enter to send)", id="chat-input")
        yield Footer()

    def on_mount(self) -> None:
        tt: DataTable = self.query_one("#todo-table", DataTable)
        tt.add_columns("done", "id", "text")
        mt: DataTable = self.query_one("#mem-table", DataTable)
        mt.add_column("memory")
        self.refresh_todos()
        self.refresh_memories()
        self.refresh_brief()
        self.query_one("#chat-log", RichLog).write("Ask anything. Writes still ask approval (auto-yes in TUI is OFF).")

    # ---- brief ----
    def refresh_brief(self) -> None:
        from .brief import gather_brief

        log = self.query_one("#brief-log", RichLog)
        log.clear()
        try:
            data = gather_brief()
        except Exception as e:
            log.write(f"[red]brief failed: {e}[/red]")
            return
        log.write(f"[bold]sidekick brief[/]  {data['when']}")
        for line in str(data["sysinfo"]).splitlines()[:8]:
            log.write(f"  {line}")
        log.write("[bold]projects[/]")
        for s in data["projects"]:
            if not s.get("exists"):
                log.write(f"  {s['path']}: missing")
            else:
                log.write(f"  {s['path']} [{s.get('branch','?')}] {s.get('changed',0)} changed")
        if data.get("todos"):
            log.write("[bold]open todos[/]")
            for i, t, _ in data["todos"][:5]:
                log.write(f"  ○ #{i} {t}")
        if data.get("memories"):
            log.write("[bold]memories[/]")
            for m in data["memories"][:5]:
                log.write(f"  • {m}")

    @on(Button.Pressed, "#brief-refresh")
    def _brief_btn(self) -> None:
        self.refresh_brief()

    # ---- todos ----
    def refresh_todos(self) -> None:
        from .store import list_todos

        tt: DataTable = self.query_one("#todo-table", DataTable)
        tt.clear()
        for i, t, d in list_todos(open_only=False)[-50:]:
            tt.add_row("✓" if d else "○", str(i), t[:100])

    @on(Input.Submitted, "#todo-input")
    def _todo_add(self, ev: Input.Submitted) -> None:
        from .store import add_todo

        if ev.value.strip():
            add_todo(ev.value.strip()[:500])
            ev.input.clear()
            self.refresh_todos()

    @on(Button.Pressed, "#todo-done")
    def _todo_done_focus(self) -> None:
        self.query_one("#todo-input", Input).focus()

    @on(Button.Pressed, "#todo-clear")
    def _todo_clear(self) -> None:
        from .store import clear_todos

        clear_todos()
        self.refresh_todos()

    # ---- memories ----
    def refresh_memories(self) -> None:
        from .store import list_memories

        mt: DataTable = self.query_one("#mem-table", DataTable)
        mt.clear()
        for m in list_memories(limit=50):
            mt.add_row(m[:120])

    @on(Input.Submitted, "#mem-input")
    def _mem_add(self, ev: Input.Submitted) -> None:
        from .store import save_memory

        if ev.value.strip():
            save_memory(ev.value.strip()[:2000])
            ev.input.clear()
            self.refresh_memories()

    @on(Button.Pressed, "#mem-forget")
    def _mem_forget(self) -> None:
        inp: Input = self.query_one("#mem-input", Input)
        from .store import forget_memory

        if inp.value.strip():
            forget_memory(inp.value.strip())
            inp.clear()
            self.refresh_memories()

    # ---- chat ----
    @on(Input.Submitted, "#chat-input")
    def _chat_send(self, ev: Input.Submitted) -> None:
        text = ev.value.strip()
        if not text:
            return
        ev.input.clear()
        self.run_worker(self._chat_answer(text), exclusive=True)

    async def _chat_answer(self, text: str) -> None:
        import asyncio

        from .agent import run_agent
        from .config import Config
        from .store import get_history, save_message

        log = self.query_one("#chat-log", RichLog)
        log.write(f"[bold green]you>[/] {text}")
        cfg = Config.load()
        if self.model_override:
            cfg.model = self.model_override
        save_message("tui", "user", text)
        hist = get_history("tui")
        chunks: list[str] = []

        def on_token(tok: str) -> None:
            chunks.append(tok)

        try:
            answer = await asyncio.to_thread(run_agent, text, hist, cfg, None, on_token, None)
        except Exception as e:
            log.write(f"[red]Error: {e}[/red]")
            return
        save_message("tui", "assistant", answer)
        # stream may have been reasoning-heavy; show final compact answer
        shown = "".join(chunks)
        if len(shown) < len(answer or "") * 0.5:
            log.write(f"[cyan]sidekick>[/] {answer}")
        else:
            log.write("")
        self.refresh_todos()
        self.refresh_memories()


def launch(model: str = "") -> None:
    SidekickTUI(model=model).run()
