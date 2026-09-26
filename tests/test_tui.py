"""TUI tests: pilot mount + chat behaviors (no LLM needed)."""

import re

from sk.tui import ChatArea, SidekickTUI


def _blob(app) -> str:
    return "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)


def test_role_builder_styles():
    from sk.tui import _line

    you = _line("12:00", "you", "hi [x]")
    assert any("34f5a2" in str(s.style) for s in you.spans)
    assert "hi [x]" in you.plain  # brackets literal, body neutral
    bot = _line("12:00", "sidekick", "hello")
    assert any("9d7bff" in str(s.style) for s in bot.spans)
    tool = _line("12:00", "tool", "○ tool: x")
    assert any("8fa698" in str(s.style) for s in tool.spans)
    assert _line("12:00", "", "plain").plain.startswith("[12:00] ")


def test_help_text_covers_all_bindings():
    """Help can't rot: every keybinding appears in the F1 text."""
    from sk.tui import SidekickTUI
    from sk.tui.widgets import ChatArea

    def keys(bindings):
        out = set()
        for b in bindings:
            out.add(b[0] if isinstance(b, tuple) else b.key)
        return out

    app = SidekickTUI()
    text = app._help_text()
    for key in keys(SidekickTUI.BINDINGS) | keys(ChatArea.BINDINGS):
        assert key in text, f"binding {key!r} missing from help"
    assert "pageup" in text and "ctrl+g" in text
    assert "Recently changed" in text  # keymap migration note


def test_theme_toggle_and_roles():
    from sk.tui import theme as theme_mod
    from sk.tui.theme import (
        DARK_NAME,
        LIGHT_NAME,
        active_roles,
        current_name,
        mode_for_name,
        name_for_mode,
        set_theme,
        toggle_theme,
    )

    assert name_for_mode("light") == LIGHT_NAME
    assert name_for_mode("dark") == DARK_NAME
    assert name_for_mode("bogus") == DARK_NAME
    assert mode_for_name(LIGHT_NAME) == "light"
    assert mode_for_name(DARK_NAME) == "dark"

    class FakeApp:
        def __init__(self):
            self.theme = DARK_NAME

        def register_theme(self, theme):
            pass

    fake = FakeApp()
    try:
        set_theme(fake, LIGHT_NAME)
        assert current_name() == LIGHT_NAME
        assert "0a7d4f" in active_roles()["you"]
        assert toggle_theme(fake) == DARK_NAME
        assert "34f5a2" in active_roles()["you"]
        assert set_theme(fake, "bogus") == DARK_NAME  # unknown keeps current
    finally:
        # restore suite-wide default for other tests
        set_theme(fake, DARK_NAME)
        theme_mod._current = DARK_NAME


async def _pilot_checks():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        assert app.query_one("#chat-log") is not None
        assert app.query_one("#chat-input", ChatArea) is not None
        assert app.query_one("#live") is not None
        assert "/help" in app.sub_title
        await pilot.pause()


async def _pilot_enter_submits(monkeypatch):
    import sk.agent as agent

    monkeypatch.setattr(agent, "run_agent", lambda *a, **k: "ok done")
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "hello test"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        blob = _blob(app)
        assert "hello test" in blob and "ok done" in blob
        assert area.text == ""


async def _pilot_thinking_animates(monkeypatch):
    import threading as _th

    import sk.agent as agent

    started = _th.Event()
    release = _th.Event()

    def blocking_fake(*a, **k):
        started.set()
        release.wait(timeout=15)
        return "eventual answer"

    monkeypatch.setattr(agent, "run_agent", blocking_fake)
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "slow question here"
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            if started.is_set():
                break
        assert started.is_set()
        live = app.query_one("#live")
        live_blob = "\n".join(str(ln) for ln in live.lines)
        assert "thinking" in live_blob  # animated indicator before tokens
        release.set()
        for _ in range(30):
            await pilot.pause()
            if "eventual answer" in _blob(app):
                break
        assert "eventual answer" in _blob(app)


async def _pilot_multiline():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "line one"
        area.cursor_to_end()
        await pilot.pause()
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert area.text == "line one\n"
        area.clear()
        area.text = "ab"
        area.cursor_to_end()
        await pilot.pause()
        await pilot.press("alt+enter")
        await pilot.pause()
        assert area.text == "ab\n"


async def _pilot_history(tmp_path=None, monkeypatch=None):
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.cmd_history = ["first cmd", "second cmd"]
        area.focus()
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        assert area.text == "second cmd"
        await pilot.press("up")
        await pilot.pause()
        assert area.text == "first cmd"
        await pilot.press("down")
        await pilot.pause()
        assert area.text == "second cmd"


async def _pilot_slash_help():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "/help"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        blob = _blob(app)
        assert "/model" in blob and "[fast|smart|name]" in blob


async def _pilot_slash_model():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "/model fast"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.model_override == "llama3.2:3b"
        assert "llama" in app.sub_title


async def _pilot_streaming(monkeypatch):
    import sk.agent as agent

    def fake(
        text,
        hist,
        cfg,
        on_tool=None,
        on_token=None,
        approve=None,
        on_reasoning=None,
        auto_approve=False,
        review_plan=None,
    ):
        if on_reasoning:
            on_reasoning("hmm ")
        for tok in ("Hello", " world"):
            if on_token:
                on_token(tok)
        return "# Hi\n\n- a"

    monkeypatch.setattr(agent, "run_agent", fake)
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "hi there friend"
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause()
            try:
                if "Hi" in _blob(app):
                    break
            except Exception:
                pass
        blob = _blob(app)
        assert "Hi" in blob
        assert "you>" in blob and "sidekick>" in blob  # role markers rendered
        assert re.search(r"\d+s · ~\d+tok", app.sub_title)


async def _pilot_ctrl_y():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        # focus the input: this is the real failing scenario, TextArea's
        # builtin ctrl+y (redo) used to swallow the keystroke.
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        blob = _blob(app)
        assert "cop" in blob  # copied... or copy failed hint


def test_mic_crash_logged(tmp_path, monkeypatch):
    import sk.config as _c
    from sk.tui import log_error

    monkeypatch.setattr(_c, "CONFIG_DIR", tmp_path)
    log_error("mic-toggle", RuntimeError("boom"))
    content = (tmp_path / "tui-errors.log").read_text()
    assert "mic-toggle" in content and "boom" in content


async def _pilot_timestamps():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "/help"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        blob = _blob(app)
        assert re.search(r"\[\d\d:\d\d\]", blob)


def _run(coro):
    import asyncio

    asyncio.run(coro)


async def _pilot_live_hidden_after_completion(monkeypatch):
    from textual.widgets import RichLog

    import sk.agent as agent

    seen_on_token = {"cb": None}

    def streaming_fake(*args, **k):
        import time

        on_token = args[4] if len(args) > 4 else k.get("on_token")
        seen_on_token["cb"] = on_token
        if on_token:
            on_token("the ")
            time.sleep(0.2)
            on_token("answer.")
        return "the answer."

    monkeypatch.setattr(agent, "run_agent", streaming_fake)
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "hi"
        await pilot.press("enter")
        streamed_live = False
        for _ in range(80):
            await pilot.pause()
            live = app.query_one("#live", RichLog)
            if app._live_n >= 1:
                streamed_live = True
            if live.styles.display == "none":
                break
        assert seen_on_token["cb"] is not None, "on_token must be wired to run_agent"
        assert streamed_live, "live area should stream the answer before completion"
        live = app.query_one("#live", RichLog)
        assert app._live_parts == [], "live parts should reset after completion"
        assert live.styles.display == "none", "live area must be hidden after completion"


def test_live_hidden_after_completion(monkeypatch):
    _run(_pilot_live_hidden_after_completion(monkeypatch))


def test_pilot_mount():
    _run(_pilot_checks())


def test_enter_submits(monkeypatch):
    _run(_pilot_enter_submits(monkeypatch))


def test_thinking_animates(monkeypatch):
    _run(_pilot_thinking_animates(monkeypatch))


async def _pilot_help_overlay():
    from sk.tui import SidekickTUI as _T

    app = _T()
    async with app.run_test() as pilot:
        panel = app.query_one("#help-panel")
        await pilot.pause()
        assert not panel.display
        await pilot.press("f1")
        await pilot.pause()
        assert panel.display
        blob = "\n".join(str(ln) for ln in panel.lines)
        assert "ctrl+y" in blob and "/model" in blob
        await pilot.press("f1")
        await pilot.pause()
        assert not panel.display
        await pilot.press("f1")
        await pilot.pause()
        assert panel.display
        await pilot.press("escape")
        await pilot.pause()
        assert not panel.display


def test_help_overlay():
    _run(_pilot_help_overlay())


def test_empty_state_hint():
    from sk.tui import SidekickTUI as _T

    async def _go():
        app = _T()
        async with app.run_test() as pilot:
            await pilot.pause()
            blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
            assert "New here?" in blob  # isolated DB is always fresh

    _run(_go())


def test_ctrl_j_newline():
    _run(_pilot_multiline())


def test_history_arrows():
    _run(_pilot_history())


def test_slash_help_renders():
    _run(_pilot_slash_help())


def test_slash_model_switches():
    _run(_pilot_slash_model())


def test_streaming_and_stats(monkeypatch):
    _run(_pilot_streaming(monkeypatch))


def test_ctrl_y_copies():
    _run(_pilot_ctrl_y())


def test_launch_mouse_on():
    import sk.tui as tui_mod
    from textual.app import App

    seen = {}

    def fake_run(self, **kwargs):
        seen.update(kwargs)

    orig = App.run
    App.run = fake_run  # type: ignore
    try:
        tui_mod.launch()
    finally:
        App.run = orig  # type: ignore
    assert seen.get("mouse") is True  # drag-select + auto-copy on release


async def _pilot_copy_selection(monkeypatch):
    import sk.clip as _c
    from textual.geometry import Offset
    from textual.selection import Selection

    from sk.tui import SidekickTUI as _T

    copied: list[str] = []
    monkeypatch.setattr(_c, "backends_available", lambda: ["xclip"])
    monkeypatch.setattr(_c, "copy_text", lambda t: copied.append(t) or "xclip")
    app = _T()
    async with app.run_test(notifications=True) as pilot:
        log = app.query_one("#chat-log")
        log.clear()
        log.write("SELECTME line one")
        log.write("other line")
        await pilot.pause()
        # drag-select "SELEC" (row 0, cols 0-5) the way a mouse drag would
        app.screen.selections[log] = Selection(Offset(0, 0), Offset(5, 0))
        await pilot.pause()
        assert app.screen.get_selected_text() == "SELEC"
        app.action_copy_last()
        await pilot.pause()
        assert copied == ["SELEC"]
        await pilot.pause()  # let the confirmation toast mount
        assert "copied selection via xclip" in _toast_blob(app).lower()


def test_ctrl_y_copies_selection(monkeypatch):
    _run(_pilot_copy_selection(monkeypatch))


async def _pilot_mouse_up_copies(monkeypatch):
    import sk.clip as _c
    from textual.geometry import Offset
    from textual.selection import Selection
    from textual.widgets import RichLog

    from sk.tui import SidekickTUI as _T

    copied: list[str] = []
    monkeypatch.setattr(_c, "backends_available", lambda: ["xclip"])
    monkeypatch.setattr(_c, "copy_text", lambda t: copied.append(t) or "xclip")

    class Up:
        button = 1

    app = _T()
    async with app.run_test() as pilot:
        log = app.query_one("#chat-log")
        log.clear()
        log.write("AUTO line one")
        await pilot.pause()
        app.screen.selections[log] = Selection(Offset(0, 0), Offset(4, 0))
        await pilot.pause()
        log.on_mouse_up(Up())  # release: auto-copy like a real drag
        await pilot.pause()
        assert copied == ["AUTO"]
        log.on_mouse_up(Up())  # same selection again: no duplicate copy
        await pilot.pause()
        assert copied == ["AUTO"]
        assert isinstance(log, RichLog)


def test_mouse_up_copies(monkeypatch):
    _run(_pilot_mouse_up_copies(monkeypatch))


async def _pilot_ctrl_y_warn(monkeypatch):
    import sk.clip as _c
    import sk.store as _s
    from sk.tui import SidekickTUI as _T

    monkeypatch.setattr(_c, "backends_available", lambda: [])
    monkeypatch.setattr(
        _s, "get_history", lambda *a, **k: [{"role": "assistant", "content": "ans"}]
    )
    app = _T()
    async with app.run_test(notifications=True) as pilot:
        area = app.query_one("#chat-input")
        area.focus()
        await pilot.pause()
        await pilot.press("ctrl+y")
        await pilot.pause()
        await pilot.pause()
        blob = _toast_blob(app).lower()
        assert "xclip" in blob  # honest warning instead of fake success


def test_ctrl_y_warns_without_backends(monkeypatch):
    _run(_pilot_ctrl_y_warn(monkeypatch))


def _span_bgs(strip):
    """Map cell ranges of a rendered strip to (bgcolor, fgcolor)."""
    out = {}
    col = 0
    for seg in strip:
        out[(col, col + seg.cell_length)] = (seg.style.bgcolor, seg.style.color)
        col += seg.cell_length
    return out


def _toast_blob(app):
    """Visible toast popups joined as plain text."""
    from textual.widgets._toast import Toast

    return "\n".join(str(t.render()) for t in app.screen.query(Toast))


async def _pilot_drag_selects_and_highlights(monkeypatch):
    import sk.clip as _c
    from rich.style import Style
    from textual import events

    from sk.tui import SidekickTUI as _T

    monkeypatch.setattr(_c, "backends_available", lambda: ["xclip"])
    copied: list[str] = []
    monkeypatch.setattr(_c, "copy_text", lambda t: copied.append(t) or "xclip")

    def mouse(event_cls, x, y, **kw):
        return event_cls(
            widget=None,
            x=x,
            y=y,
            delta_x=0,
            delta_y=0,
            button=1,
            shift=False,
            meta=False,
            ctrl=False,
            screen_x=x,
            screen_y=y,
            style=Style(),
            **kw,
        )

    app = _T()
    async with app.run_test(size=(120, 40)) as pilot:
        from rich.text import Text

        log = app.query_one("#chat-log")
        log.clear()
        log.write(Text("line one abc"))
        log.write(Text("line two xyz"))
        log.write(Text("line three"))
        await pilot.pause()

        region = app.screen.find_widget(log).region
        x0, y0 = region.x + 3, region.y + 1
        x1, y1 = region.x + 8, region.y + 2
        app.post_message(mouse(events.MouseDown, x0, y0))
        for step in range(1, 6):
            app.post_message(
                mouse(
                    events.MouseMove,
                    x0 + (x1 - x0) * step // 5,
                    y0 + (y1 - y0) * step // 5,
                )
            )
            await pilot.pause()
        app.post_message(mouse(events.MouseUp, x1, y1))
        await pilot.pause()
        await pilot.pause()

        # a real drag copies the substring, and the highlight clears on release
        assert copied == ["ne one abc\nline two"]
        assert app.screen.selections.get(log) is None
        assert not app.screen.get_selected_text()


def test_drag_selects_and_highlights(monkeypatch):
    _run(_pilot_drag_selects_and_highlights(monkeypatch))


async def _pilot_selection_highlight_visible(monkeypatch):
    from textual.geometry import Offset
    from textual.selection import Selection

    from sk.tui import SidekickTUI as _T

    app = _T()
    async with app.run_test(size=(120, 40)) as pilot:
        from rich.text import Text

        log = app.query_one("#chat-log")
        log.clear()
        log.write(Text("line one abc"))
        log.write(Text("line two xyz"))
        log.write(Text("line three"))
        await pilot.pause()

        plain = _span_bgs(log.render_line(0))
        assert len(plain) >= 2  # text + padding filler exist

        # select columns 2..6 of row 0
        log.screen.selections[log] = Selection(Offset(2, 0), Offset(6, 0))
        await pilot.pause()

        colormap = _span_bgs(log.render_line(0))
        plain_bg = next(iter(plain.values()))[0]
        highlighted = [(rng, bg, fg) for rng, (bg, fg) in colormap.items() if bg != plain_bg]
        # exactly the selected cells (2..6) change background
        assert highlighted, "selection produced no visible background change"
        assert [lo for (lo, hi), bg, fg in highlighted] == [2]
        assert all(hi == 6 for (lo, hi), bg, fg in highlighted)
        # text stays legible: the selected fg is the normal text color, not the bg
        for (lo, hi), bg, fg in highlighted:
            assert fg == next(iter(plain.values()))[1], "selected text fg changed"

        # selection spanning two rows highlights both, nothing below the end row
        log.screen.selections[log] = Selection(Offset(2, 0), Offset(6, 1))
        await pilot.pause()
        row0 = _span_bgs(log.render_line(0))
        row1 = _span_bgs(log.render_line(1))
        row2 = _span_bgs(log.render_line(2))
        assert any(bg != plain_bg for (lo, hi), (bg, fg) in row0.items())
        assert any(bg != plain_bg for (lo, hi), (bg, fg) in row1.items())
        assert all(bg == plain_bg for (lo, hi), (bg, fg) in row2.items())


def test_selection_highlight_visible(monkeypatch):
    _run(_pilot_selection_highlight_visible(monkeypatch))


def test_timestamps():
    _run(_pilot_timestamps())


def _approval_fake(store):
    def fake(
        text,
        hist,
        cfg,
        on_tool=None,
        on_token=None,
        approve=None,
        on_reasoning=None,
        auto_approve=False,
        review_plan=None,
    ):
        ok = approve("write_file", {"path": "/tmp/x", "content": "hi"})
        store.append(ok)
        return "wrote it" if ok else "blocked"

    return fake


async def _pilot_approval_yes(monkeypatch):
    import sk.agent as agent

    from sk.tui import SidekickTUI as _T

    calls: list[bool] = []
    monkeypatch.setattr(agent, "run_agent", _approval_fake(calls))
    app = _T()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input")
        area.focus()
        area.text = "write something"
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            if "allow write_file" in _blob(app):
                break
        assert "allow write_file" in _blob(app)
        area.focus()
        area.text = "y"
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause()
            if "wrote it" in _blob(app):
                break
        assert calls == [True]
        assert "approved" in _blob(app) and "wrote it" in _blob(app)


async def _pilot_approval_no(monkeypatch):
    import sk.agent as agent

    from sk.tui import SidekickTUI as _T

    calls: list[bool] = []
    monkeypatch.setattr(agent, "run_agent", _approval_fake(calls))
    app = _T()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input")
        area.focus()
        area.text = "write something"
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            if "allow write_file" in _blob(app):
                break
        area.focus()
        area.text = "n"
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause()
            if "denied" in _blob(app):
                break
        assert calls == [False]
        assert "denied" in _blob(app)


def test_approval_fast_paths():
    from sk.tui import SidekickTUI

    app = SidekickTUI()
    assert app._approve("list_dir", {}) is True  # reads pass, no UI needed
    app.state["yolo"] = True
    assert app._approve("write_file", {"path": "/tmp/x"}) is True


def test_tui_approval_yes(monkeypatch):
    _run(_pilot_approval_yes(monkeypatch))


def test_tui_approval_no(monkeypatch):
    _run(_pilot_approval_no(monkeypatch))


async def _pilot_dash_yes_approves():
    from sk.tui import SidekickTUI as _T

    app = _T()
    async with app.run_test() as pilot:
        import threading

        ev = threading.Event()
        # live pending as a real worker would leave it (worker clears it)
        app._pending_approval = {
            "question": "write_file -> /tmp/x",
            "event": ev,
            "answer": False,
            "asked_at": 0,
            "reply": "",
            "token": object(),
            "owner": threading.get_ident(),
            "deadline": 9999999999.0,
        }
        area = app.query_one("#chat-input")
        area.focus()
        area.text = "--yes"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "approved" in blob
        assert ev.is_set() and app._pending_approval["answer"] is True


def test_dash_yes_approves():
    _run(_pilot_dash_yes_approves())


def test_affirmative_words():
    from sk.tui import is_affirmative

    for yes in (
        "y",
        "yes",
        "yeah",
        "yep",
        "go ahead",
        "go ahead and do it",
        "do it",
        "--yes",
        "-y",
        "YES",
        "  Yup  ",
    ):
        assert is_affirmative(yes), yes
    for no in ("n", "no", "nope", "yesterday", "yeah but not there", "ok, wait", ""):
        assert not is_affirmative(no), no


async def _pilot_stale_pending_ignored(monkeypatch):
    import threading
    import time as _t

    import sk.agent as agent

    from sk.tui import SidekickTUI as _T

    agent_calls: list[str] = []

    def fake(
        text,
        hist,
        cfg,
        on_tool=None,
        on_token=None,
        approve=None,
        on_reasoning=None,
        auto_approve=False,
        review_plan=None,
    ):
        agent_calls.append(text)
        return "agent heard you"

    monkeypatch.setattr(agent, "run_agent", fake)
    app = _T()
    async with app.run_test() as pilot:
        # stale slot: dead owner thread + blown deadline (crashed worker)
        app._pending_approval = {
            "question": "write_file -> /tmp/z",
            "event": threading.Event(),
            "answer": False,
            "asked_at": _t.monotonic() - 999,
            "reply": "",
            "token": object(),
            "owner": 42424242,
            "deadline": _t.monotonic() - 10,
        }
        area = app.query_one("#chat-input")
        area.focus()
        area.text = "hello agent"
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            if agent_calls:
                break
        assert agent_calls == ["hello agent"]  # not eaten by the stale slot
        assert app._pending_approval is None  # stale slot reaped


async def _pilot_quit_releases_pending(monkeypatch):
    import threading
    import time as _t

    from sk.tui import SidekickTUI as _T

    app = _T()
    async with app.run_test() as pilot:
        app._pending_approval = {
            "question": "write_file -> /tmp/z",
            "event": threading.Event(),
            "answer": False,
            "asked_at": _t.monotonic(),
            "reply": "",
            "token": object(),
            "owner": threading.get_ident(),  # live owner: would block real flow
            "deadline": _t.monotonic() + 300,
        }
        area = app.query_one("#chat-input")
        area.focus()
        area.text = "/quit"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app._pending_approval is None  # released, not left dangling


async def _pilot_slash_bypasses_pending(monkeypatch):
    import threading
    import time as _t

    import sk.agent as agent

    from sk.tui import SidekickTUI as _T

    agent_calls: list[str] = []

    def fake(
        text,
        hist,
        cfg,
        on_tool=None,
        on_token=None,
        approve=None,
        on_reasoning=None,
        auto_approve=False,
        review_plan=None,
    ):
        agent_calls.append(text)
        return "done"

    monkeypatch.setattr(agent, "run_agent", fake)
    app = _T()
    async with app.run_test() as pilot:
        app._pending_approval = {
            "question": "write_file -> /tmp/z",
            "event": threading.Event(),
            "answer": False,
            "asked_at": _t.monotonic(),
            "reply": "",
            "token": object(),
            "owner": threading.get_ident(),
            "deadline": _t.monotonic() + 300,
        }
        area = app.query_one("#chat-input")
        area.focus()
        area.text = "/help"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "/model" in blob  # slash ran instead of being eaten
        assert agent_calls == []  # and no agent turn started
        assert app._pending_approval is not None  # still waiting for y/n


def test_stale_pending_ignored(monkeypatch):
    _run(_pilot_stale_pending_ignored(monkeypatch))


def test_quit_releases_pending(monkeypatch):
    _run(_pilot_quit_releases_pending(monkeypatch))


def test_slash_bypasses_pending(monkeypatch):
    _run(_pilot_slash_bypasses_pending(monkeypatch))


def test_approval_timeout_denies(monkeypatch):
    """Nobody answers: short timeout denies and says it was a timeout."""
    import threading

    import sk.tui as _tmod
    from sk.tui import SidekickTUI

    app = SidekickTUI()
    app._approve_timeout = 0.2
    posted: list[str] = []
    monkeypatch.setattr(app, "call_from_thread", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(_tmod, "_role", lambda log, role, body: posted.append(f"{role}:{body}"))
    monkeypatch.setattr(_tmod, "_w", lambda *a, **k: None)

    class _Stub:
        def write(self, item):
            posted.append(f"write:{item}")

        def focus(self):
            posted.append("focus")

    monkeypatch.setattr(app, "query_one", lambda *a, **k: _Stub())
    t0 = __import__("time").monotonic()
    assert app._approve("write_file", {"path": "/tmp/x"}) is False
    assert __import__("time").monotonic() - t0 < 30
    assert any("timed out" in p or "no answer" in p for p in posted)


def test_mount_shows_build():
    from sk.tui import SidekickTUI as _T

    async def _go():
        app = _T()
        async with app.run_test() as pilot:
            await pilot.pause()
            blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
            assert "build " in blob and "sk version" in blob

    _run(_go())


async def _pilot_slash_complete():
    from sk.tui import SidekickTUI as _T

    app = _T()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input")
        area.focus()
        area.text = "/"
        await pilot.pause()
        await pilot.pause()
        lst = app.query_one("#slash-list")
        assert lst.display and len(lst.children) >= 10
        area.text = "/mo"
        await pilot.pause()
        await pilot.pause()
        kids = list(lst.children)
        assert kids and app._slash_names
        assert all(n.split()[0].startswith("mo") for n in app._slash_names)
        await pilot.press("enter")
        await pilot.pause()
        assert area.text.startswith("/model ")
        # esc dismisses
        area.text = "/x"
        await pilot.pause()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not app.query_one("#slash-list").display
        # plain text hides list
        area.text = "hello"
        await pilot.pause()
        await pilot.pause()
        assert not app.query_one("#slash-list").display


def test_slash_complete():
    _run(_pilot_slash_complete())


async def _pilot_scroll_keys():
    from sk.tui import SidekickTUI as _T

    app = _T()
    async with app.run_test(size=(80, 24)) as pilot:
        log = app.query_one("#chat-log")
        for i in range(100):
            log.write(f"line {i}")
        await pilot.pause()
        area = app.query_one("#chat-input")
        area.focus()
        await pilot.pause()
        assert log.scroll_y == log.max_scroll_y  # tail-followed on write
        await pilot.press("pageup")
        await pilot.pause()
        assert log.scroll_y < log.max_scroll_y
        up_at = log.scroll_y
        await pilot.press("pagedown")
        await pilot.pause()
        assert log.scroll_y > up_at
        await pilot.press("ctrl+home")
        await pilot.pause()
        assert log.scroll_y == 0
        await pilot.press("ctrl+end")
        await pilot.pause()
        assert log.scroll_y == log.max_scroll_y


def test_scroll_keys():
    _run(_pilot_scroll_keys())


class _FakeProc:
    returncode = 0

    def terminate(self):
        pass

    def wait(self, timeout=None):
        pass


def _mock_voice(monkeypatch, text="hello from mic"):
    import sk.voice as _v

    monkeypatch.setattr(_v, "check_mic", lambda: (True, "mic ready"))
    monkeypatch.setattr(_v, "ensure_stt", lambda: (True, "stt ready"))
    monkeypatch.setattr(_v, "start_recording", lambda *a, **k: _FakeProc())
    monkeypatch.setattr(_v, "stop_recording", lambda proc, timeout=5, wav_path="": None)
    monkeypatch.setattr(_v, "transcribe", lambda *a, **k: text)


async def _pilot_mic_roundtrip(monkeypatch):
    from sk.tui import SidekickTUI as _T

    _mock_voice(monkeypatch)
    app = _T()
    async with app.run_test() as pilot:
        app.action_mic()  # ctrl+g path: pill is display-only
        await pilot.pause()
        assert app.mic_state == "recording"
        app.action_mic()  # stop
        for _ in range(30):
            await pilot.pause()
            if "hello from mic" in app.query_one("#chat-input").text:
                break
        assert "hello from mic" in app.query_one("#chat-input").text
        assert app.mic_state == "idle"
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "heard>" in blob


async def _pilot_mic_no_stt(monkeypatch):
    import sk.voice as _v
    from sk.tui import SidekickTUI as _T

    monkeypatch.setattr(_v, "check_mic", lambda: (True, "mic ready"))
    monkeypatch.setattr(_v, "ensure_stt", lambda: (False, "faster-whisper not installed"))
    app = _T()
    async with app.run_test() as pilot:
        app.action_mic()
        await pilot.pause()
        await pilot.pause()
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "sk talk --install" in blob
        assert app.mic_state == "idle"
        assert app.mic_state == "idle"
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "heard>" in blob


async def _pilot_mic_no_stt(monkeypatch):
    import sk.voice as _v
    from sk.tui import SidekickTUI as _T

    monkeypatch.setattr(_v, "check_mic", lambda: (True, "mic ready"))
    monkeypatch.setattr(_v, "ensure_stt", lambda: (False, "faster-whisper not installed"))
    app = _T()
    async with app.run_test() as pilot:
        app.action_mic()
        await pilot.pause()
        await pilot.pause()
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "sk talk --install" in blob


def test_mic_roundtrip(monkeypatch):
    _run(_pilot_mic_roundtrip(monkeypatch))


def test_mic_no_stt_hint(monkeypatch):
    _run(_pilot_mic_no_stt(monkeypatch))


async def _pilot_status_bar():
    from textual.widgets import Static

    from sk.tui import SidekickTUI as _T

    app = _T()
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one("#status-bar", Static)
        assert bar.display
        text = str(bar.render())
        assert app.sub_title in text  # bar mirrors sub_title (session shown short)
        app._stats = "12s · ~3tok"
        app._sub()
        await pilot.pause()
        assert "12s" in str(bar.render())


async def _pilot_sessions_drawer(monkeypatch):
    import sk.store as store
    from textual.widgets import ListView

    from sk.tui import ChatArea
    from sk.tui import SidekickTUI as _T

    store.save_message("drawer-a", "user", "hello alpha")
    store.save_message("drawer-b", "user", "hello beta")
    app = _T()
    async with app.run_test() as pilot:
        drawer = app.query_one("#sessions-drawer", ListView)
        area = app.query_one("#chat-input", ChatArea)
        assert not drawer.display
        await pilot.press("f3")
        await pilot.pause()
        assert drawer.display
        assert set(app._drawer_sessions) >= {"drawer-a", "drawer-b"}
        # select a row -> switches session and renders its tail
        drawer.index = app._drawer_sessions.index("drawer-b")
        await pilot.press("enter")
        await pilot.pause()
        assert app.session == "drawer-b"
        assert not drawer.display  # auto-hides after select
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "hello beta" in blob
        # esc closes an open drawer and refocuses input
        await pilot.press("f3")
        await pilot.pause()
        assert drawer.display
        await pilot.press("escape")
        await pilot.pause()
        assert not drawer.display
        assert area.has_focus


async def _pilot_drawer_lists_new_session(monkeypatch):
    import sk.store as store
    from textual.widgets import ListView

    from sk.tui import SidekickTUI as _T

    app = _T()
    async with app.run_test() as pilot:
        drawer = app.query_one("#sessions-drawer", ListView)
        await pilot.press("f3")
        await pilot.pause()
        before = set(app._drawer_sessions)
        await pilot.press("f3")  # close
        await pilot.pause()
        store.save_message("drawer-fresh", "user", "brand new")
        await pilot.press("f3")  # reopen refreshes
        await pilot.pause()
        assert drawer.display
        assert "drawer-fresh" in set(app._drawer_sessions) - before


def test_status_bar():
    _run(_pilot_status_bar())


def test_sessions_drawer():
    _run(_pilot_sessions_drawer(None))


def test_drawer_lists_new_session():
    _run(_pilot_drawer_lists_new_session(None))


async def _pilot_approval_card():
    from sk.tui import SidekickTUI as _T

    app = _T()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._ask_approval("write_file", "/tmp/x", "hello content", 300)
        await pilot.pause()
        await pilot.pause()
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "allow write_file" in blob
        assert "/tmp/x" in blob and "[y/N]" in blob
        assert "timeout denies" in blob


async def _pilot_live_throttle():
    from textual.widgets import RichLog

    from sk.tui import SidekickTUI as _T

    app = _T()
    async with app.run_test() as pilot:
        await pilot.pause()
        live = app.query_one("#live", RichLog)
        app._prime_live()  # display:block, otherwise RichLog defers rendering
        await pilot.pause()
        app._stop_think_timer()  # dots would interleave with assertions
        app._live_parts = ["hello **world**"]
        app._live_reason = []
        app._push_live()
        await pilot.pause()
        first_ts = app._live_rendered_at
        assert first_ts > 0
        before = len(live.lines)
        assert before >= 1
        for _ in range(5):  # flood within the throttle window: no re-renders
            app._push_live()
        await pilot.pause()
        assert len(live.lines) == before
        assert app._live_rendered_at == first_ts
        app._live_rendered_at = 0  # force expiry
        app._push_live()
        await pilot.pause()
        assert len(live.lines) == before  # clear + single rewrite, no growth
        blob = "\n".join(str(ln) for ln in live.lines)
        assert "world" in blob


async def _pilot_live_fence_and_reason():
    from textual.widgets import RichLog

    from sk.tui import SidekickTUI as _T

    app = _T()
    async with app.run_test() as pilot:
        await pilot.pause()
        live = app.query_one("#live", RichLog)
        app._prime_live()  # display:block, otherwise RichLog defers rendering
        await pilot.pause()
        app._stop_think_timer()  # dots would interleave with assertions
        app._live_parts = ["```python\nprint(1)\n"]  # unclosed fence: must not crash
        app._live_reason = ["hmm thinking"]
        app._live_rendered_at = 0
        app._push_live()  # must not raise
        await pilot.pause()
        blob = "\n".join(str(ln) for ln in live.lines)
        assert "hmm thinking" in blob and "print" in blob  # highlighted code splits tokens


def test_approval_card():
    _run(_pilot_approval_card())


def test_live_throttle():
    _run(_pilot_live_throttle())


def test_live_fence_and_reason():
    _run(_pilot_live_fence_and_reason())


async def _pilot_reasoning_beats_dots():
    from textual.widgets import RichLog

    from sk.tui import SidekickTUI as _T

    app = _T()
    async with app.run_test() as pilot:
        await pilot.pause()
        live = app.query_one("#live", RichLog)
        app._prime_live()  # dots ticking, no tokens yet
        await pilot.pause()
        assert app._think_timer is not None
        app._live_reason.append("weighing two plans")
        app._push_live()
        await pilot.pause()
        blob = "\n".join(str(ln) for ln in live.lines)
        assert "weighing two plans" in blob  # uniform reasoning block renders
        app._think_tick()  # reasoning present: dots must stop, not clobber
        await pilot.pause()
        assert app._think_timer is None
        blob = "\n".join(str(ln) for ln in live.lines)
        assert "weighing two plans" in blob


async def _pilot_reasoning_stays_display_only(monkeypatch):
    import sk.agent as agent

    def fake(
        text,
        hist,
        cfg,
        on_tool=None,
        on_token=None,
        approve=None,
        on_reasoning=None,
        auto_approve=False,
        review_plan=None,
    ):
        if on_reasoning:
            on_reasoning("secret deliberation")
        if on_token:
            on_token("Final")
        return "Final"

    monkeypatch.setattr(agent, "run_agent", fake)
    app = SidekickTUI()
    async with app.run_test() as pilot:
        area = app.query_one("#chat-input", ChatArea)
        area.focus()
        area.text = "decide something"
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            try:
                if "Final" in _blob(app):
                    break
            except Exception:
                pass
        blob = _blob(app)
        assert "Final" in blob
        assert "secret deliberation" not in blob  # reasoning never saved
        assert app._think_timer is None


def test_reasoning_beats_dots():
    _run(_pilot_reasoning_beats_dots())


def test_reasoning_stays_display_only(monkeypatch):
    _run(_pilot_reasoning_stays_display_only(monkeypatch))
