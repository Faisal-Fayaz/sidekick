"""TUI ChatLog + ChatArea widgets (split from sk/tui.py, pure move)."""

from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import RichLog, TextArea

from .helpers import _save_history


class ChatLog(RichLog):
    """Chat history with working mouse-drag text selection.

    Stock RichLog renders RichVisual, which the base get_selection() can't
    extract text from — so drag-selection in it copies nothing. We extract
    from the stored line texts instead.
    """

    ALLOW_SELECT = True

    def get_selection(self, selection) -> tuple[str, str] | None:
        try:
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
        Binding("escape", "slash_dismiss", "dismiss", show=False),
        Binding("tab", "slash_complete", "complete", show=False),
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
        text: str = self.text  # type: ignore[has-type]  # TextArea reactive
        if self.app.slash_complete_active(text):
            self.app.slash_complete()
            return
        text = text.strip()
        if text:
            self.hist_idx = -1
            self.post_message(ChatArea.Send(text))
        self.clear()

    def action_slash_complete(self) -> None:
        if not self.app.slash_complete_active():
            self.insert("    ")
            return

    def action_slash_dismiss(self) -> None:
        if self.app.close_help_if_open():
            return
        self.app.slash_dismiss()

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
        text: str = self.text  # type: ignore[has-type]  # TextArea reactive
        lines = text.split("\n")
        end = (len(lines) - 1, len(lines[-1]))
        try:
            sel = self.selection  # type: ignore[has-type]  # TextArea reactive
            self.selection = type(sel)(end, end)
        except Exception:
            pass

    def _browse(self, step: int) -> None:
        if self.app.slash_navigate(step):
            return
        text: str = self.text  # type: ignore[has-type]  # TextArea reactive
        if "\n" in text or not self.cmd_history:
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
