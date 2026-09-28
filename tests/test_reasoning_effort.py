"""Reasoning-effort control (#201): parsing, config, request plumbing. No network."""

from sk.agent import _extra_body
from sk.config import Config, _parse_reasoning_effort


def _cfg(**kw):
    base = {
        "provider": "openrouter",
        "model": "stealth/space-bunny-alpha",
        "base_url": "",
        "api_key": "k",
        "max_steps": 5,
        "temperature": 0.2,
    }
    base.update(kw)
    return Config(**base)


def test_parse_levels():
    assert _parse_reasoning_effort("high") == "high"
    assert _parse_reasoning_effort("MAX") == "max"
    assert _parse_reasoning_effort("  low  ") == "low"
    assert _parse_reasoning_effort("turbo") == "low"
    assert _parse_reasoning_effort("") == "low"
    assert _parse_reasoning_effort(None) is not None and _parse_reasoning_effort(None) == "low"


def test_config_default_env_file(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    assert Config.load().reasoning_effort == "low"
    monkeypatch.setenv("SIDEKICK_REASONING_EFFORT", "high")
    assert Config.load().reasoning_effort == "high"
    monkeypatch.delenv("SIDEKICK_REASONING_EFFORT")
    monkeypatch.setenv("SIDEKICK_REASONING_EFFORT", "turbo")
    assert Config.load().reasoning_effort == "low"  # garbage falls back
    monkeypatch.delenv("SIDEKICK_REASONING_EFFORT")


def test_config_round_trip(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    cfg = _cfg()
    cfg.reasoning_effort = "medium"
    cfg.save()
    assert Config.load().reasoning_effort == "medium"


def test_extra_body_matrix():
    assert _extra_body(_cfg()) == {"reasoning": {"effort": "low"}}
    assert _extra_body(_cfg(), plan_mode=True) == {"reasoning": {"effort": "high"}}
    off = _cfg()
    off.reasoning_effort = "off"
    assert _extra_body(off) == {}
    assert _extra_body(off, plan_mode=True) == {"reasoning": {"effort": "high"}}
    local = _cfg(provider="ollama", model="qwen2.5-coder:7b")
    body = _extra_body(local)
    assert "reasoning" not in body and "options" in body  # local knobs untouched
    assert _extra_body(_cfg(provider="openai", model="gpt-4o")) == {}
    assert _extra_body(_cfg(provider="custom", model="x")) == {}


def test_run_agent_escalates_on_plan(tmp_path, monkeypatch):
    import sk.agent as agent
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    seen = {}

    class _Msg:
        content = "done"
        tool_calls = None
        reasoning = ""
        finish = "stop"

    def fake_stream(client, model, messages, tools, temperature, max_tokens, extra, *a, **k):
        seen["extra"] = extra
        return _Msg()

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    agent.run_agent("summarize this", [], _cfg(), session="s")
    assert seen["extra"] == {"reasoning": {"effort": "low"}}
    agent.run_agent("summarize this", [], _cfg(), session="s", plan_mode=True)
    assert seen["extra"] == {"reasoning": {"effort": "high"}}


def test_config_cli_set_and_show(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    res = CliRunner().invoke(app, ["config", "--reasoning-effort", "high"])
    assert res.exit_code == 0, res.output
    res = CliRunner().invoke(app, ["config", "--show"])
    assert res.exit_code == 0 and "reasoning_effort=high" in res.output
    res = CliRunner().invoke(app, ["config", "--reasoning-effort", "turbo"])
    assert res.exit_code == 0
    res = CliRunner().invoke(app, ["config", "--show"])
    assert "reasoning_effort=low" in res.output
