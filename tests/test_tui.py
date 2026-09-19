"""TUI tests: pilot mount + chat behaviors (no LLM needed)."""

import re

from sk.tui import ChatArea, SidekickTUI


def _blob(app) -> str:
    return "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)


def test_role_builder_styles():
    from sk.tui import _line

    you = _line("12:00", "you", "hi [x]")
    assert any("green" in str(s.style) for s in you.spans)
    assert "hi [x]" in you.plain  # brackets literal, body neutral
    bot = _line("12:00", "sidekick", "hello")
    assert any("cyan" in str(s.style) for s in bot.spans)
    tool = _line("12:00", "tool", "○ tool: x")
    assert any("dim" in str(s.style) for s in tool.spans)
    assert _line("12:00", "", "plain").plain.startswith("[12:00] ")


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
        assert "hello test" in blob and "thinking" in blob
        assert area.text == ""


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

    def fake(text, hist, cfg, on_tool=None, on_token=None, approve=None, on_reasoning=None):
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
        assert "green" in blob and "cyan" in blob  # role colors rendered
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


def test_launch_disables_mouse():
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
    assert seen.get("mouse") is False  # native terminal selection = normal copy


def test_launch_mouse_flag():
    import sk.tui as tui_mod
    from textual.app import App

    seen = {}

    def fake_run(self, **kwargs):
        seen.update(kwargs)

    orig = App.run
    App.run = fake_run  # type: ignore
    try:
        tui_mod.launch(mouse=True)
    finally:
        App.run = orig  # type: ignore
    assert seen.get("mouse") is True


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


def test_pilot_mount():
    _run(_pilot_checks())


def test_enter_submits(monkeypatch):
    _run(_pilot_enter_submits(monkeypatch))


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


def test_timestamps():
    _run(_pilot_timestamps())


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
    monkeypatch.setattr(_v, "stop_recording", lambda proc, timeout=5: None)
    monkeypatch.setattr(_v, "transcribe", lambda *a, **k: text)


async def _pilot_mic_roundtrip(monkeypatch):
    from textual.widgets import Button

    from sk.tui import SidekickTUI as _T

    _mock_voice(monkeypatch)
    app = _T()
    async with app.run_test() as pilot:
        await pilot.click("#mic-btn")
        await pilot.pause()
        assert "stop" in str(app.query_one("#mic-btn").label).lower()
        # second activation via posted Pressed: repeated pilot.clicks don't
        # re-fire in headless mode (pilot mouse-state quirk, not app code).
        btn = app.query_one("#mic-btn", Button)
        app.post_message(Button.Pressed(btn))
        for _ in range(30):
            await pilot.pause()
            if "hello from mic" in app.query_one("#chat-input").text:
                break
        assert "hello from mic" in app.query_one("#chat-input").text
        assert "mic" in str(app.query_one("#mic-btn").label).lower()
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "heard>" in blob


async def _pilot_mic_no_stt(monkeypatch):
    import sk.voice as _v
    from sk.tui import SidekickTUI as _T

    monkeypatch.setattr(_v, "check_mic", lambda: (True, "mic ready"))
    monkeypatch.setattr(_v, "ensure_stt", lambda: (False, "faster-whisper not installed"))
    app = _T()
    async with app.run_test() as pilot:
        await pilot.click("#mic-btn")
        await pilot.pause()
        await pilot.pause()
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "sk talk --install" in blob


def test_mic_roundtrip(monkeypatch):
    _run(_pilot_mic_roundtrip(monkeypatch))


def test_mic_no_stt_hint(monkeypatch):
    _run(_pilot_mic_no_stt(monkeypatch))
