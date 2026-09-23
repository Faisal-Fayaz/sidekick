"""Suite-wide isolation: no test may touch the real ~/.sidekick/.

Regression guards:
- /model (slash + TUI) calls cfg.save(). Without this, pilot tests rewrite
  the user's live config (happened once: base_url=http://x/v1).
- TUI pilot tests save chat history via store.save_message. Without this,
  every suite run dumped ~30 fake rows ("wrote it", "blocked", ...) into the
  user's live `tui` session, confusing the agent for real (happened: 256 rows).
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")


@pytest.fixture(autouse=True)
def _isolate_history(tmp_path, monkeypatch):
    import sk.store as store_mod

    monkeypatch.setattr(store_mod, "DB_PATH", tmp_path / "history.db")
    store_mod.set_default_namespace("")
    yield
    store_mod.set_default_namespace("")
