"""CLI streaming status-line tests: stale "thinking..." must be erased on the
first streamed token / tool line so it never survives the answer (offline)."""

import io
from contextlib import redirect_stdout

from sk.cli.approvers import _make_on_token, _make_on_tool

CLEAR = "\r\x1b[2K"


def test_on_token_clears_status_once():
    out = io.StringIO()
    state = {"n": 0, "cleared": False, "clear_line": True}
    on_token = _make_on_token(state)
    with redirect_stdout(out):
        on_token("Hel")
        on_token("lo")
        on_token("!")
    assert out.getvalue() == CLEAR + "Hello!"
    assert state["n"] == 6
    assert state["cleared"] is True


def test_on_token_unset_when_no_status_line():
    out = io.StringIO()
    on_token = _make_on_token()
    with redirect_stdout(out):
        on_token("Hi")
    assert out.getvalue() == "Hi"


def test_on_tool_and_token_share_one_clear():
    lines: list[str] = []

    class _FakeConsole:
        def print(self, *a, **k):
            lines.extend(str(a) for a in a)

    import sk.cli.approvers as ap
    import sk.cli.base as base

    real = ap.console
    ap.console = _FakeConsole()
    try:
        out = io.StringIO()
        state = {"n": 0, "cleared": False, "clear_line": True}
        on_tool = _make_on_tool(state)
        on_token = _make_on_token(state)
        with redirect_stdout(out):
            on_tool("shell", {"cmd": "pwd"})
            on_token("answer")
    finally:
        ap.console = real
        base.console = real

    assert out.getvalue() == CLEAR + "answer"  # cleared exactly once, by the tool line
    assert any("○ tool: shell" in ln for ln in lines)


def test_chat_flow_no_thinking_after_completion():
    """The chat REPL prints a status line with end='' and the first streamed
    token replaces it, so 'thinking' never survives in the emitted text."""
    stream = {"n": 0, "cleared": False, "clear_line": True}
    on_token = _make_on_token(stream)

    class _FakeConsole:
        def print(self, *a, **k):
            pass

    import sk.cli.approvers as ap

    real = ap.console
    ap.console = _FakeConsole()
    try:
        ap.console.print("[dim]thinking... (streams live)[/dim]", end="")
        ap.console.print("")
    finally:
        ap.console = real

    out = io.StringIO()
    with redirect_stdout(out):
        on_token("the answer.")
        on_token("")
    text = out.getvalue()
    assert text.startswith(CLEAR)
    assert "thinking" not in text
    assert "the answer." in text
