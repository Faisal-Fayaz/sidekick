"""TUI footer key hints tests (fixes #160): hints built from BINDINGS,
visible on launch. Fully offline."""

from sk.tui import SidekickTUI


def _bindings():
    from textual.binding import Binding

    from sk.tui.widgets import ChatArea

    out = {}
    for b in list(SidekickTUI.BINDINGS) + list(ChatArea.BINDINGS):
        if isinstance(b, tuple):
            key, action, _desc = b
        elif isinstance(b, Binding):
            key, action = b.key, b.action
        else:
            continue
        out.setdefault(str(action), str(key))
    return out


def test_hints_match_bindings():
    text = SidekickTUI._key_hints_text()
    keys = _bindings()
    for action, label in SidekickTUI.HINT_ACTIONS:
        assert action in keys, f"{action} not bound"
        assert f"{keys[action]} {label}" in text
    for required in ("enter send", "f1 help", "f5 models", "ctrl+y copy"):
        assert required in text


async def _pilot_footer_visible():
    app = SidekickTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Static

        bar = app.query_one("#key-hints", Static)
        assert bar.display
        assert "f1 help" in str(bar.render())


def test_pilot_footer_visible():
    import asyncio

    asyncio.run(_pilot_footer_visible())
