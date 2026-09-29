"""QA regression suite: feature + edge-case coverage from the systematic test audit.

Policy (senior-QA convention for reporting bugs without fixing them):
- Tests asserting the *correct* behavior for a confirmed, still-open bug are marked
  ``@pytest.mark.xfail(strict=True, reason="GH-<n>: ...")``. While the bug exists
  they report as XFAIL (suite stays green); once fixed they XPASS-strict-fail,
  which is the signal to drop the marker.
- All other tests assert behavior that already holds and guard against regressions.

Every xfail references a filed issue on Faisal-Fayaz/sidekick (assignee: Irfanwani),
each verified with a minimal repro on main before filing. No test touches the live
``~/.sidekick/`` (suite conftest isolates config + history DB) and no test needs
the network (httpx/DNS are stubbed where the code path would dial out).
"""

import pytest

import sk.config as config_mod
import sk.slash as slash
import sk.store as store
from sk.config import Config

ISSUES = "https://github.com/Faisal-Fayaz/sidekick/issues"


def _ctx(tmp_path, monkeypatch):
    """Isolated slash context (mirrors tests/test_slash.py convention)."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    cfg = Config(
        model="llama3.2:3b", base_url="http://x/v1", api_key="x", max_steps=1, temperature=0.0
    )
    return {"session": "test", "cfg": cfg, "state": {"yolo": False}}


def _seed_sessions():
    store.save_message("sess-A", "user", "alpha")
    store.save_message("sess-B", "user", "beta")


# ---------------------------------------------------------------------------
# Slash dispatcher edge cases
# ---------------------------------------------------------------------------


def test_bare_slash_returns_help_not_crash(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    out = slash.handle("/", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled is True
    assert out.text  # help / unknown-command text, not an exception


def test_resume_zero_is_usage(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    _seed_sessions()
    out = slash.handle("/resume 0", session="sess-B", cfg=c["cfg"], state=c["state"])
    assert "usage" in out.text.lower()
    assert not getattr(out, "switch_session", None)
    out = slash.handle("/resume -1", session="sess-B", cfg=c["cfg"], state=c["state"])
    assert "usage" in out.text.lower()
    assert not getattr(out, "switch_session", None)
    out = slash.handle("/resume 99", session="sess-B", cfg=c["cfg"], state=c["state"])
    assert "usage" in out.text.lower()
    assert not getattr(out, "switch_session", None)


def test_sessions_delete_zero_is_usage(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    _seed_sessions()
    before = {r["session"] for r in store.list_sessions(limit=20)}
    for bad in ("0", "-1", "99"):
        out = slash.handle(
            f"/sessions delete {bad}", session="sess-B", cfg=c["cfg"], state=c["state"]
        )
        assert "usage" in out.text.lower()
    after = {r["session"] for r in store.list_sessions(limit=20)}
    assert before == after


def test_history_garbage_arg_does_not_crash(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    out = slash.handle("/history abc", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled is True


# ---------------------------------------------------------------------------
# Config robustness: garbage values must not brick the CLI
# ---------------------------------------------------------------------------


def _write_config_toml(text):
    config_mod.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config_mod.CONFIG_PATH.write_text(text)


def test_garbage_max_steps_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    _write_config_toml('max_steps = "oops"\n')
    cfg = Config.load()
    assert cfg.max_steps == 5  # DEFAULTS["max_steps"]


def test_garbage_temperature_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    _write_config_toml('temperature = "hot"\n')
    cfg = Config.load()
    assert cfg.temperature == pytest.approx(0.2)


def test_garbage_numerics_warn_and_cover_history_budget(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    _write_config_toml('max_steps = "oops"\nhistory_budget_tokens = "lots"\n')
    cfg = Config.load()
    assert cfg.max_steps == 5 and cfg.history_budget_tokens == 3000
    err = capsys.readouterr().err
    assert "max_steps" in err and "history_budget_tokens" in err


def test_valid_numerics_load(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    _write_config_toml("max_steps = 7\ntemperature = 0.5\n")
    cfg = Config.load()
    assert cfg.max_steps == 7
    assert cfg.temperature == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Tool-dispatch contract: validation failures return "Error", never raise
# ---------------------------------------------------------------------------


def test_read_url_bad_max_chars_returns_error():
    from sk.tools import registry as reg

    out = reg.dispatch_tool("read_url", {"url": "https://example.com", "max_chars": "bad"})
    assert isinstance(out, str) and out.startswith("Error")


def test_web_search_bad_count_returns_error():
    from sk.tools import registry as reg

    out = reg.dispatch_tool("web_search", {"query": "hello", "count": "bad"})
    assert isinstance(out, str) and out.startswith("Error")


def test_read_url_missing_url_returns_error():
    from sk.tools import registry as reg

    out = reg.dispatch_tool("read_url", {})
    assert isinstance(out, str) and out.startswith("Error")


def test_shell_dispatch_bad_timeout_falls_back():
    from sk.tools import registry as reg

    out = reg.dispatch_tool("shell", {"cmd": "echo hi", "timeout": "bad"})
    assert "hi" in out


# ---------------------------------------------------------------------------
# Shell destructive-command blocklist (GH-220)
# ---------------------------------------------------------------------------

BYPASSES = [
    "rm --recursive --force /",
    "rm -rf ~/",
    "rm -rf $HOME/",
    "rm -r /..",
    "echo hi > /dev/nvme0n1",
    "echo hi > /dev/mmcblk0",
    "echo hi > /dev/sda1",
    "chmod -Rf 777 /",
    "chmod --recursive 777 /",
    "chown -R nobody /",
    "x(){ x|x& };x",
]


@pytest.mark.parametrize("cmd", BYPASSES)
def test_shell_blocklist_bypass_spellings_refused(cmd):
    from sk.tools import shell as sh

    assert sh._check_shell(cmd) is not None, f"allowed through guard: {cmd!r}"


@pytest.mark.parametrize("cmd", ["rm -rf /", "chmod -R 777 /", ":(){ :|:& };:", "mkfs /dev/sda1"])
def test_shell_canonical_destructions_stay_blocked(cmd):
    """Guard behavior that already holds — lock it in against weakening."""
    from sk.tools import shell as sh

    assert sh._check_shell(cmd) is not None


def test_shell_benign_passthrough():
    from sk.tools import shell as sh

    assert sh._check_shell("echo hi") is None
    for cmd in [
        "rm -rf ./build",
        "rm --recursive --force ./dir",
        "rm -rf ~/.cache/old",
        "rm -rf /tmp/x",
        "chmod -R 755 subdir",
        "chmod 777 file",
        "echo hi > /dev/null",
        "greet() { echo hi; }",
        "dd if=/dev/zero of=/tmp/x",
        "chown -R user subdir",
    ]:
        assert sh._check_shell(cmd) is None, f"legit command blocked: {cmd!r}"


# ---------------------------------------------------------------------------
# Memory safety: forget must never mass-delete on blank input (GH-222)
# ---------------------------------------------------------------------------


def test_forget_blank_deletes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    store.save_memory("hello world foo")
    store.save_memory("another thing bar")
    out = store.forget_memory(" ")
    assert "0" in out
    assert len(store.recall_memories("x", limit=10)) == 2


def test_forget_empty_deletes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    store.save_memory("hello world foo")
    out = store.forget_memory("")
    assert "0" in out
    assert len(store.recall_memories("hello", limit=10)) == 1


def test_forget_nomatch_deletes_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    store.save_memory("hello world foo")
    out = store.forget_memory("zzz-no-such-memory")
    assert "0" in out
    assert len(store.recall_memories("hello", limit=10)) == 1


def test_forget_blank_via_slash_and_cli(tmp_path, monkeypatch):
    import sk.config as config_mod
    import sk.slash as slash_mod
    from sk.config import Config

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    store.save_memory("hello world foo")
    cfg = Config(
        model="llama3.2:3b", base_url="http://x/v1", api_key="x", max_steps=1, temperature=0.0
    )
    out = slash_mod.handle("/forget   ", session="s", cfg=cfg, state={"yolo": False})
    assert "usage" in out.text.lower()
    from typer.testing import CliRunner

    from sk.cli import app

    res = CliRunner().invoke(app, ["forget", " "])
    assert res.exit_code == 1 and "usage" in res.output.lower()
    assert len(store.recall_memories("hello", limit=10)) == 1


# ---------------------------------------------------------------------------
# Agent tool-cache key must cover content, not just target (GH-226)
# ---------------------------------------------------------------------------


def test_tool_target_distinguishes_content():
    from sk.agent import _tool_target

    a = _tool_target("write_file", {"path": "/tmp/x", "content": "A"})
    b = _tool_target("write_file", {"path": "/tmp/x", "content": "B"})
    assert a != b


def test_tool_target_same_call_is_stable():
    """Dedupe happy path that already holds — lock it in."""
    from sk.agent import _tool_target

    args = {"path": "/tmp/x", "content": "A"}
    assert _tool_target("write_file", dict(args)) == _tool_target("write_file", dict(args))


def test_tool_target_distinguishes_tools():
    from sk.agent import _tool_target

    assert _tool_target("read_file", {"path": "/tmp/x"}) != _tool_target(
        "write_file", {"path": "/tmp/x", "content": "A"}
    )


# ---------------------------------------------------------------------------
# SSRF: redirects must be re-validated (GH-221) — fully hermetic
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status=200, location=None):
        self.status_code = status
        self.headers = {"content-type": "text/html"}
        if location:
            self.headers["location"] = location
        self.text = "<html><head><title>t</title></head><body>" + "S" * 100 + "</body></html>"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _RedirectChainClient:
    """Emulates httpx with follow_redirects=False: each GET returns one hop
    (302 with Location, or the final 200) so redirect handling is observable."""

    instances = []

    def __init__(self, *a, **k):
        self.hops = []
        _RedirectChainClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kw):
        self.hops.append(url)
        if url == "https://example.com/page":
            return _FakeResp(302, location="http://127.0.0.1:11434/private")
        if url == "https://example.com/ok":
            return _FakeResp(302, location="https://example.com/final")
        return _FakeResp(200)


def test_read_url_does_not_fetch_redirect_to_private(monkeypatch):
    import httpx

    from sk.tools import web as webmod

    _RedirectChainClient.instances.clear()
    real_blocked = webmod._url_blocked

    def _allow_decoy(url):
        if "example.com" in url:
            return None  # public decoy: skip real DNS so the test stays offline
        return real_blocked(url)

    monkeypatch.setattr(webmod, "_url_blocked", _allow_decoy)
    monkeypatch.setattr(httpx, "Client", _RedirectChainClient)
    out = webmod.tool_read_url("https://example.com/page")
    hops = _RedirectChainClient.instances[-1].hops
    assert not any("127.0.0.1" in h or "169.254" in h for h in hops), hops
    assert isinstance(out, str) and out.startswith("Error")


def test_read_url_follows_redirect_to_public(monkeypatch):
    import httpx

    from sk.tools import web as webmod

    _RedirectChainClient.instances.clear()
    real_blocked = webmod._url_blocked

    def _allow_decoy(url):
        if "example.com" in url:
            return None  # public decoy: skip real DNS so the test stays offline
        return real_blocked(url)

    monkeypatch.setattr(webmod, "_url_blocked", _allow_decoy)
    monkeypatch.setattr(httpx, "Client", _RedirectChainClient)
    out = webmod.tool_read_url("https://example.com/ok")
    hops = _RedirectChainClient.instances[-1].hops
    assert hops == ["https://example.com/ok", "https://example.com/final"]
    assert isinstance(out, str) and not out.startswith("Error")


def test_direct_private_url_stays_blocked():
    """Guard behavior that already holds — lock it in against weakening."""
    from sk.tools import web as webmod

    assert webmod.tool_read_url("http://127.0.0.1:11434/").startswith("Error")
    assert webmod.tool_read_url("http://169.254.169.254/").startswith("Error")


# ---------------------------------------------------------------------------
# CLI UX: clean errors, clamped limits
# ---------------------------------------------------------------------------


def test_cwd_missing_dir_clean_error():
    from typer.testing import CliRunner

    from sk.cli import app

    r = CliRunner().invoke(app, ["--cwd", "/no/such/dirXYZ-qa", "config", "--show"])
    assert r.exception is None
    assert r.exit_code != 0


@pytest.mark.xfail(
    strict=True, reason=f"GH-229 ({ISSUES}/229): list_shell(-5) returns the whole table"
)
def test_negative_shell_limit_clamped(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    for i in range(60):
        store.log_shell(f"run-qa-probe-{i}", cwd="/tmp", exit=0)
    rows = store.list_shell(limit=-5)
    assert len(rows) <= 50


def test_positive_shell_limit_respected(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    for i in range(10):
        store.log_shell(f"run-qa-probe-{i}", cwd="/tmp", exit=0)
    assert len(store.list_shell(limit=5)) == 5
