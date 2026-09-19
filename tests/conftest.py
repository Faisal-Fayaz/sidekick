"""Suite-wide isolation: no test may touch the real ~/.sidekick/config.toml.

Regression guard: /model (slash + TUI) calls cfg.save(). Without this,
pilot tests rewrite the user's live config (happened once: base_url=http://x/v1).
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
