"""TUI model picker popup tests (fixes #130): list builder, F5 open/filter/
select/dismiss, fetch-failure fallback. Pilot tests isolate network via mock."""

import sk.store as store
from sk.tui import SidekickTUI


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    monkeypatch.setattr(
        "sk.auth.fetch_models", lambda *a, **k: ["qwen2.5-coder:7b", "llama3.2:3b", "fast"]
    )


def test_choices_marks_current_dedupes(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    app.model_override = "qwen2.5-coder:7b"
    rows = app._model_choices()
    ids = [mid for mid, _ in rows]
    assert ids[0] == "qwen2.5-coder:7b"
    assert rows[0][1].startswith("●")
    assert "fast" in ids and "smart" in ids
    assert ids.count("fast") == 1 and ids.count("qwen2.5-coder:7b") == 1
    assert "llama3.2:3b" in ids


def test_choices_fetch_failure_falls_back(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr("sk.auth.fetch_models", boom)
    app = SidekickTUI()
    ids = [mid for mid, _ in app._model_choices()]
    assert "fast" in ids and "smart" in ids
    assert len(ids) == 3  # current + fast + smart


def test_choose_model_bad_index_noop(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    app._drawer_models = ["fast"]
    app._choose_model(99)
    assert app.model_override == ""


def test_choose_model_resolves_and_persists(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    import sk.config as config_mod

    app = SidekickTUI()
    app._drawer_models = ["fast"]
    app._choose_model(0)
    assert app.model_override == "llama3.2:3b"
    assert config_mod.CONFIG_PATH.exists()


def _blob(app) -> str:
    return "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)


def _picker_items(app) -> list:
    from textual.widgets import ListView

    return list(app.query_one("#model-list", ListView).children)


async def _pilot_open_filter_select(monkeypatch, tmp_path):
    from textual.widgets import Input

    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    async with app.run_test() as pilot:
        await pilot.press("f5")
        await pilot.pause()
        from textual.containers import Vertical

        assert app.query_one("#model-picker", Vertical).display
        assert len(_picker_items(app)) == 4  # current + fast + smart + llama
        filt = app.query_one("#model-filter", Input)
        filt.value = "qwen"
        await pilot.pause()
        await pilot.pause()
        assert len(_picker_items(app)) == 1
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app.model_override == "qwen2.5-coder:7b"
        assert "model →" in _blob(app)
        assert not app.query_one("#model-picker", Vertical).display


async def _pilot_escape_dismisses(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    async with app.run_test() as pilot:
        await pilot.press("f5")
        await pilot.pause()
        from textual.containers import Vertical

        assert app.query_one("#model-picker", Vertical).display
        await pilot.press("escape")
        await pilot.pause()
        assert not app.query_one("#model-picker", Vertical).display
        assert app.model_override == ""


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_pilot_open_filter_select(monkeypatch, tmp_path):
    _run(_pilot_open_filter_select(monkeypatch, tmp_path))


def test_pilot_escape_dismisses(monkeypatch, tmp_path):
    _run(_pilot_escape_dismisses(monkeypatch, tmp_path))
