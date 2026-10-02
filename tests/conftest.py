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
def _isolate_module_globals():
    """Snapshot and restore process-wide module globals tests mutate.

    `agent._tools_unsupported` and `hooks._started_sessions` are module-level
    sets. Tests added to and cleared them without restoring, so the suite passed
    only because of ordering: `test_eval.py:496` clearing the set is the only
    reason later tests saw a clean one (#321).

    `fsperm._repaired` is the same kind of one-shot flag and leaked the same way:
    whichever test ran first consumed it, so `test_repair_once_only_sweeps_the_
    first_time` passed alone and failed in the suite. Reset before every test
    rather than only in the ones that know about it.
    """
    import sk.agent as agent_mod
    import sk.hooks as hooks_mod

    import sk.fsperm as fsperm_mod

    fsperm_mod.reset_repair_flag()
    saved = (set(agent_mod._tools_unsupported), set(hooks_mod._started_sessions))
    try:
        yield
    finally:
        agent_mod._tools_unsupported.clear()
        agent_mod._tools_unsupported.update(saved[0])
        hooks_mod._started_sessions.clear()
        hooks_mod._started_sessions.update(saved[1])


@pytest.fixture(autouse=True)
def _isolate_history(tmp_path, monkeypatch):
    import sk.store as store_mod

    monkeypatch.setattr(store_mod, "DB_PATH", tmp_path / "history.db")
    store_mod.set_default_namespace("")
    yield
    store_mod.set_default_namespace("")


# Public hosts the suite fakes. A module that stubs httpx and wants to keep
# testing SSRF/redirect behaviour opts in with `pytestmark = pytest.mark.usefixtures(...)`.
# Not autouse: the point of #328 is that network access is denied unless asked for.
EGRESS_TEST_HOSTS = (
    "example.com",
    "*.example.com",
    "example.org",
    "duckduckgo.com",
    "*.duckduckgo.com",
)


@pytest.fixture
def egress_test_hosts(monkeypatch):
    """Grant the fake-network hosts this suite uses. Opt-in, per module.

    Call it directly with extra hosts when a test needs a decoy outside the
    shared set: `egress_test_hosts("img")` appends to it.
    """
    import sk.config as config_mod

    extra: tuple[str, ...] = ()
    real_load = config_mod.Config.load

    def _load(*a, **kw):
        cfg = real_load(*a, **kw)
        cfg.egress_allow = EGRESS_TEST_HOSTS + extra
        return cfg

    monkeypatch.setattr(config_mod.Config, "load", staticmethod(_load))

    def _add(*hosts: str) -> None:
        nonlocal extra
        extra = extra + tuple(hosts)

    return _add
