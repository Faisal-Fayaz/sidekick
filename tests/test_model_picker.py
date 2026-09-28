"""TUI model picker popup tests (fixes #130): list builder, F5 open/filter/
select/dismiss, fetch-failure fallback. Pilot tests isolate network via mock."""

import sk.store as store
from sk.tui import SidekickTUI


def _iso(tmp_path, monkeypatch, keys=None, live=None):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    monkeypatch.setattr("sk.keyring.get_key", lambda p: (keys or {}).get(p, ""))
    default_live = ["qwen2.5-coder:7b", "llama3.2:3b", "fast"] if live is None else live

    def fake_fetch(provider, *a, **k):
        if isinstance(default_live, dict):
            v = default_live.get(provider, [])
            if isinstance(v, Exception):
                raise v
            return list(v)
        return list(default_live) if provider == "ollama" else []

    monkeypatch.setattr("sk.auth.fetch_models", fake_fetch)


def test_choices_marks_current_dedupes(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    app.model_override = "qwen2.5-coder:7b"
    rows = app._model_choices()
    ids = [mid for _, mid, _ in rows]
    assert ids[0] == "qwen2.5-coder:7b"
    assert rows[0][2].startswith("●")
    assert "fast" in ids and "smart" in ids
    assert ids.count("fast") == 1 and ids.count("qwen2.5-coder:7b") == 1
    assert "llama3.2:3b" in ids
    assert all(p is None for p, _, _ in rows)  # single provider here


def test_choices_fetch_failure_falls_back(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr("sk.auth.fetch_models", boom)
    app = SidekickTUI()
    ids = [mid for _, mid, _ in app._model_choices()]
    assert "fast" in ids and "smart" in ids
    assert len(ids) == 3  # current + fast + smart


def test_choose_model_bad_index_noop(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    app = SidekickTUI()
    app._drawer_models = [(None, "fast")]
    app._choose_model(99)
    assert app.model_override == ""


def test_choose_model_resolves_and_persists(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    import sk.config as config_mod

    app = SidekickTUI()
    app._drawer_models = [(None, "fast")]
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


async def _pilot_grouped_cross_provider_select(monkeypatch, tmp_path):
    from textual.containers import Vertical
    from textual.widgets import Input

    _iso(
        tmp_path,
        monkeypatch,
        keys={"groq": "k-groq"},
        live={"ollama": ["qwen2.5-coder:7b"], "groq": ["openai/gpt-oss-20b"]},
    )
    app = SidekickTUI()
    async with app.run_test() as pilot:
        await pilot.press("f5")
        await pilot.pause()
        assert app.query_one("#model-picker", Vertical).display
        assert len(_picker_items(app)) == 4  # current + fast + smart + groq row
        filt = app.query_one("#model-filter", Input)
        filt.value = "groq"
        await pilot.pause()
        await pilot.pause()
        assert len(_picker_items(app)) == 1
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app.model_override == "openai/gpt-oss-20b"
        assert "provider →" in _blob(app)
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


def test_pilot_grouped_cross_provider_select(monkeypatch, tmp_path):
    _run(_pilot_grouped_cross_provider_select(monkeypatch, tmp_path))


def test_pilot_escape_dismisses(monkeypatch, tmp_path):
    _run(_pilot_escape_dismisses(monkeypatch, tmp_path))


def test_choices_shows_all_live_models(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    many = [f"model-{i:03d}" for i in range(60)]
    monkeypatch.setattr("sk.auth.fetch_models", lambda *a, **k: many)
    app = SidekickTUI()
    ids = [mid for _, mid, _ in app._model_choices()]
    for m in many:
        assert m in ids


def test_grouped_sections_per_provider(tmp_path, monkeypatch):
    _iso(
        tmp_path,
        monkeypatch,
        keys={"groq": "k-groq"},
        live={"ollama": ["qwen2.5-coder:7b"], "groq": ["openai/gpt-oss-20b", "llama-3.3-70b"]},
    )
    app = SidekickTUI()
    rows = app._model_choices()
    groq_rows = [(p, m, label) for p, m, label in rows if p == "groq"]
    assert [m for _, m, _ in groq_rows] == ["openai/gpt-oss-20b", "llama-3.3-70b"]
    assert all("groq ›" in label for _, _, label in groq_rows)
    first_groq = next(i for i, (p, _, _) in enumerate(rows) if p == "groq")
    assert all(p is None for p, _, _ in rows[:first_groq])  # current provider first


def test_unreachable_provider_skipped(tmp_path, monkeypatch):
    _iso(
        tmp_path,
        monkeypatch,
        keys={"groq": "k-groq"},
        live={"ollama": ["qwen2.5-coder:7b"], "groq": RuntimeError("down")},
    )
    app = SidekickTUI()
    rows = app._model_choices()
    assert all(p != "groq" for p, _, _ in rows)
    assert any(m == "qwen2.5-coder:7b" for _, m, _ in rows)


def test_keyless_provider_not_queried(tmp_path, monkeypatch):
    seen = []
    _iso(tmp_path, monkeypatch)

    def fake_fetch(provider, *a, **k):
        seen.append(provider)
        return []

    monkeypatch.setattr("sk.auth.fetch_models", fake_fetch)
    SidekickTUI()._model_choices()
    assert "ollama" in seen and "lmstudio" in seen  # local: always listed
    assert "groq" not in seen and "openai" not in seen  # clouds need keys


def test_provider_key_rules(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch, keys={"groq": "k-groq"})
    from sk.config import Config

    app = SidekickTUI()
    cfg = Config.load()  # provider ollama in tests
    assert app._provider_key(cfg, "ollama") is not None
    assert app._provider_key(cfg, "lmstudio") is not None
    assert app._provider_key(cfg, "groq")[1] == "k-groq"
    assert app._provider_key(cfg, "openai") is None
    assert app._provider_key(cfg, "custom") is None
    assert app._provider_key(cfg, "nope") is None


def test_choose_cross_provider_switches_and_persists(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch, keys={"groq": "k-groq"})
    import sk.config as config_mod

    app = SidekickTUI()
    app._drawer_models = [(None, "fast"), ("groq", "openai/gpt-oss-20b")]
    app._choose_model(1)
    assert app.model_override == "openai/gpt-oss-20b"
    cfg = config_mod.Config.load()
    assert cfg.provider == "groq" and cfg.model == "openai/gpt-oss-20b"
