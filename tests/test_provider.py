"""Provider tests: presets, overrides, masking, client wiring. Offline."""

import sk.config as config_mod
from sk.agent import get_client
from sk.config import PRESETS, Config


def test_ollama_defaults():
    cfg = Config(provider="ollama", model="qwen2.5-coder:7b", base_url="", api_key="", max_steps=5, temperature=0.2)
    assert cfg.effective_base_url() == "http://localhost:11434/v1"
    assert cfg.effective_api_key() == "ollama"


def test_cloud_preset_needs_key():
    cfg = Config(provider="openai", model="gpt-4o-mini", base_url="", api_key="", max_steps=5, temperature=0.2)
    assert cfg.effective_base_url() == "https://api.openai.com/v1"
    assert cfg.effective_api_key() == ""


def test_custom_override_wins():
    cfg = Config(provider="openai", model="m", base_url="https://proxy.local/v1", api_key="k", max_steps=5, temperature=0.2)
    assert cfg.effective_base_url() == "https://proxy.local/v1"


def test_all_presets_have_urls():
    for name, p in PRESETS.items():
        if name == "custom":
            continue
        assert p["base_url"].startswith("http"), name
        assert p["model"], name


def test_mask():
    assert Config.mask("") == "(none)"
    assert Config.mask("short") == "****"
    assert Config.mask("sk-abcdef123456") == "sk-…3456"


def test_client_uses_effective_values():
    cfg = Config(provider="groq", model="m", base_url="", api_key="gsk-test", max_steps=5, temperature=0.2)
    client = get_client(cfg)
    assert "groq" in str(client.base_url)


def test_extra_body_local_only():
    from sk.agent import _extra_body

    ollama = Config(provider="ollama", model="m", base_url="", api_key="", max_steps=5, temperature=0.2)
    assert _extra_body(ollama) == {"options": {"num_ctx": 4096, "num_predict": 350}}
    for prov in ("openai", "groq", "together", "deepseek", "openrouter", "custom"):
        cfg = Config(provider=prov, model="m", base_url="", api_key="k", max_steps=5, temperature=0.2)
        assert _extra_body(cfg) == {}, prov  # cloud 400s on Ollama-only 'options'


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "c.toml")
    monkeypatch.setenv("SIDEKICK_PROVIDER", "deepseek")
    monkeypatch.setenv("SIDEKICK_API_KEY", "envkey")
    monkeypatch.setenv("SIDEKICK_MODEL", "deepseek-reasoner")
    cfg = Config.load()
    assert (cfg.provider, cfg.model) == ("deepseek", "deepseek-reasoner")
    assert cfg.effective_api_key() == "envkey"
    for v in ("SIDEKICK_PROVIDER", "SIDEKICK_API_KEY", "SIDEKICK_MODEL"):
        monkeypatch.delenv(v, raising=False)


def test_save_chmod_and_reload(tmp_path, monkeypatch):
    import os

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = Config(provider="openai", model="gpt-4o-mini", base_url="", api_key="secret123456", max_steps=5, temperature=0.2)
    cfg.save()
    assert (tmp_path / "config.toml").exists()
    assert oct(os.stat(tmp_path / "config.toml").st_mode)[-3:] == "600"
    assert Config.load().effective_api_key() == "secret123456"


def test_slash_provider(monkeypatch, tmp_path):
    import sk.slash as slash
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    cfg = Config(provider="ollama", model="qwen2.5-coder:7b", base_url="", api_key="", max_steps=5, temperature=0.2)
    out = slash.handle("/provider", session="s", cfg=cfg, state={})
    assert "ollama" in out.text
    out = slash.handle("/provider groq", session="s", cfg=cfg, state={})
    assert cfg.provider == "groq" and "groq" in out.text
    assert "unknown" in slash.handle("/provider nope", session="s", cfg=cfg, state={}).text.lower()


def test_provider_tiers():
    from sk.config import provider_tier

    assert provider_tier("ollama", "fast", "d") == "llama3.2:3b"
    assert provider_tier("ollama", "smart", "d") == "qwen2.5-coder:7b"
    assert provider_tier("groq", "fast", "d") == "openai/gpt-oss-20b"
    assert provider_tier("groq", "smart", "d") == "openai/gpt-oss-120b"
    assert provider_tier("openrouter", "fast", "mydefault") == "mydefault"  # unverified -> fallback
    assert provider_tier("custom", "smart", "mydefault") == "mydefault"


def test_google_preset():
    from sk.config import PRESETS, provider_tier

    for name in ("google", "gemini"):
        assert PRESETS[name]["base_url"].startswith("https://")
        assert "gemini" in PRESETS[name]["model"]
    assert provider_tier("google", "fast", "d") == "gemini-2.5-flash"
    assert provider_tier("gemini", "smart", "d") == "gemini-2.5-pro"
