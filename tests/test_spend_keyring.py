"""Keyring + spend caps (#30): OS backend fallback, project policy, cap gate."""

import sk.auth as auth
import sk.config as config_mod
import sk.keyring as kr
from sk.agent import _spend_blocked
from sk.config import Config, _parse_spend_cap, load_project_values


def test_parse_spend_cap():
    assert _parse_spend_cap("") == 0.0
    assert _parse_spend_cap("0") == 0.0
    assert _parse_spend_cap("2.5") == 2.5
    assert _parse_spend_cap("-3") == 0.0
    assert _parse_spend_cap("garbage") == 0.0
    assert _parse_spend_cap(None) == 0.0


def test_project_file_cannot_set_spend_cap(tmp_path):
    p = tmp_path / ".sidekick.toml"
    p.write_text("spend_cap_usd = 99.0\n[project]\ndocs = []\n")
    vals, warnings = load_project_values(p)
    assert "spend_cap_usd" not in vals
    assert any("spend policy" in w for w in warnings)


def test_keyring_no_backend_never_raises(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    assert kr.backend() == ""
    assert kr.get_key("openai") == ""
    assert kr.set_key("openai", "sk-x") is False
    assert kr.delete_key("openai") is False


def test_effective_key_prefers_file_over_keyring(monkeypatch):
    monkeypatch.setattr(kr, "get_key", lambda p: "kr-key")
    cfg = Config(provider="openai", model="m", base_url="", api_key="file-key")
    assert cfg.effective_api_key() == "file-key"
    cfg2 = Config(provider="openai", model="m", base_url="", api_key="")
    assert cfg2.effective_api_key() == "kr-key"


def test_store_api_key_falls_back_to_file(monkeypatch):
    monkeypatch.setattr(kr, "set_key", lambda p, s: False)
    assert auth.store_api_key("openai", "sk-x") == "file"
    monkeypatch.setattr(kr, "set_key", lambda p, s: True)
    assert auth.store_api_key("openai", "sk-x") == "keyring"


def test_key_source_order(monkeypatch, tmp_path):
    monkeypatch.setattr(kr, "get_key", lambda p: "")
    cfg = Config(provider="ollama", model="m", base_url="", api_key="")
    assert auth.key_source(cfg) == "preset"
    monkeypatch.setattr(kr, "get_key", lambda p: "kr-key")
    assert auth.key_source(cfg) == "keyring"
    cfg.api_key = "file-key"
    assert auth.key_source(cfg) == "file"
    monkeypatch.setenv("SIDEKICK_API_KEY", "env-key")
    assert auth.key_source(cfg) == "env"


def test_spend_blocked_gating(monkeypatch, tmp_path):
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    cfg = Config(provider="openai", model="m", base_url="", api_key="k", spend_cap_usd=0.0)
    assert _spend_blocked("s", cfg) is None  # unlimited never blocks

    cfg.spend_cap_usd = 1.0
    # _spend_blocked imports usage_stats from sk.store inside the fn
    monkeypatch.setattr(store, "usage_stats", lambda *a, **k: {"cost_usd": 0.1})
    assert _spend_blocked("s", cfg) is None

    monkeypatch.setattr(store, "usage_stats", lambda *a, **k: {"cost_usd": 2.0})
    msg = _spend_blocked("s", cfg)
    assert msg is not None and "Spend cap reached" in msg and "sk config --spend-cap" in msg

    def _boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(store, "usage_stats", _boom)
    assert _spend_blocked("s", cfg) is None  # fail-open


def test_run_agent_blocked_without_llm(monkeypatch, tmp_path):
    import sk.agent as agent
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    cfg = Config(provider="ollama", model="m", base_url="", api_key="", spend_cap_usd=0.5)
    monkeypatch.setattr(store, "usage_stats", lambda *a, **k: {"cost_usd": 5.0})
    called = {}

    def _fail(*a, **k):
        called["hit"] = True
        raise AssertionError("llm must not be called")

    monkeypatch.setattr(agent, "get_client", _fail)
    out = agent.run_agent("hello", [], cfg, session="s")
    assert "Spend cap reached" in out
    assert "hit" not in called
