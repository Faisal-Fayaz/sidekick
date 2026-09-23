"""TUI ChatLog + ChatArea widgets (split from sk/tui.py, pure move)."""

from __future__ import annotations

from rich.segment import Segment
from rich.style import Style as RichStyle
from textual.binding import Binding
from textual.color import Color
from textual.message import Message
from textual.strip import Strip
from textual.style import Style as TextualStyle
from textual.widgets import RichLog, TextArea

from .helpers import _save_history


class ChatLog(RichLog):
    """Chat history with working mouse-drag text selection.

    Stock RichLog repaints cached strips and never lays down per-cell offset
    meta, so the compositor can't derive a drag range (a real drag silently
    selects the whole log) and the ``screen--selection`` highlight never
    renders. We fix both here:

    - every returned strip carries ``offset`` meta per segment, so drags
      resolve to a real substring in the widget's content (row = stored line,
      col = cell column), and
    - when the widget has a live selection, the selected cells are painted
      with the ``screen--selection`` component style.
    """

    ALLOW_SELECT = True

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        row = scroll_y + y
        width = self.scrollable_content_region.width
        strip = self._render_line(row, scroll_x, width).apply_style(self.rich_style)
        selection = self.text_selection
        if selection is not None:
            span = selection.get_span(row)
            if span is not None:
                strip = self._apply_selection_span(strip, span, scroll_x)
        return self._assign_offsets(strip, row, scroll_x)

    def _assign_offsets(self, strip: Strip, row: int, scroll_x: int) -> Strip:
        """Attach ``offset`` meta so the compositor can map a drag to cells."""
        col = scroll_x or 0
        segments: list[Segment] = []
        for seg in strip:
            style = seg.style
            offset_style = RichStyle.from_meta({"offset": (col, row)})
            styled = style + offset_style if style is not None else offset_style
            segments.append(Segment(seg.text, styled, seg.control))
            col += seg.cell_length
        return Strip(segments, strip.cell_length)

    def _apply_selection_span(self, strip: Strip, span, scroll_x: int) -> Strip:
        """Paint ``screen--selection`` over the cells in ``span`` (content cols,
        already scrolled by ``scroll_x`` on this widget).

        Mirrors how Textual styles Content selections: the translucent
        selection background is pre-blended over the cell's own background and
        the text foreground is left untouched, so selected text stays legible.
        Segments straddling either edge are split at the exact column so the
        highlight never leaks past the dragged range.
        """
        start, end = span
        width = strip.cell_length
        sx = max(0, start - scroll_x)
        ex = width if end < 0 else min(width, end - scroll_x)
        if sx >= ex:
            return strip
        selection_style = TextualStyle.from_styles(
            self.screen.get_component_styles("screen--selection")
        )
        overlay = selection_style.background
        if overlay is None or overlay.a == 0:
            return strip
        fallback_bg = self.rich_style.bgcolor
        col = 0
        segments: list[Segment] = []

        def restyled(seg: Segment) -> Segment:
            base = seg.style.bgcolor if seg.style is not None else fallback_bg
            base_color = Color.from_rich_color(base) if base is not None else Color.parse("black")
            blended = base_color + overlay
            overlay_style = RichStyle(bgcolor=blended.rich_color)
            style = seg.style + overlay_style if seg.style is not None else overlay_style
            return Segment(seg.text, style, seg.control)

        for seg in strip:
            seg_len = seg.cell_length
            seg_lo, seg_hi = col, col + seg_len
            if seg_hi > sx and seg_lo < ex:
                left = max(0, sx - seg_lo)
                right = min(seg_len, ex - seg_lo)
                seg_out = seg
                if left > 0:
                    pre, seg_out = seg_out.split_cells(left)
                    if pre.cell_length:
                        segments.append(pre)
                    right -= left
                if right >= seg_out.cell_length:
                    if seg_out.cell_length:
                        segments.append(restyled(seg_out))
                elif right > 0:
                    mid, post = seg_out.split_cells(right)
                    if mid.cell_length:
                        segments.append(restyled(mid))
                    if post.cell_length:
                        segments.append(post)
                elif seg_out.cell_length:
                    segments.append(seg_out)
            else:
                segments.append(seg)
            col += seg_len
        return Strip(segments, strip.cell_length)

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
