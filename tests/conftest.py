"""Suite-wide isolation: no test may touch the real ~/.sidekick/.

Regression guards:
- /model (slash + TUI) calls cfg.save(). Without this, pilot tests rewrite
  the user's live config (happened once: base_url=http://x/v1).
- TUI pilot tests save chat history via store.save_message. Without this,
  every suite run dumped ~30 fake rows ("wrote it", "blocked", ...) into the
  user's live `tui` session, confusing the agent for real (happened: 256 rows).

One import happens here on purpose. `sk.cli.base` builds a module-level
`Console()`, and the TUI pilot imports `sk.cli` from inside a running Textual
app. Textual redirects stdout to `_PrintCapture`, whose `isatty()` returns
True, so if this module were first imported there, Rich would permanently stamp
TRUECOLOR onto the shared singleton and every later CliRunner assertion that
greps `res.output` would see unexpected ANSI escapes. The suite passed only
because alphabetical ordering put test_tui.py after the tests it broke; one file
rename or `-p randomly` turned it red (closes #317). Importing here builds the
singleton under pytest's non-tty stdout, once, before any pilot can poison it.
"""

import pytest

import sk.cli  # noqa: F401  (build the Rich Console singleton under non-tty stdout)


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
