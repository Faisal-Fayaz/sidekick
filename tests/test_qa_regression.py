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


@pytest.mark.xfail(strict=True, reason=f"GH-223 ({ISSUES}/223): bare '/' crashes with IndexError")
def test_bare_slash_returns_help_not_crash(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    out = slash.handle("/", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled is True
    assert out.text  # help / unknown-command text, not an exception


@pytest.mark.xfail(strict=True, reason=f"GH-227 ({ISSUES}/227): '/resume 0' resumes last session")
def test_resume_zero_is_usage(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    _seed_sessions()
    out = slash.handle("/resume 0", session="sess-B", cfg=c["cfg"], state=c["state"])
    assert "usage" in out.text.lower()
    assert getattr(out, "switch_session", None) is None


@pytest.mark.xfail(
    strict=True, reason=f"GH-227 ({ISSUES}/227): '/sessions delete 0' deletes last session"
)
def test_sessions_delete_zero_is_usage(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    _seed_sessions()
    before = {r["session"] for r in store.list_sessions(limit=20)}
    out = slash.handle("/sessions delete 0", session="sess-B", cfg=c["cfg"], state=c["state"])
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


@pytest.mark.xfail(strict=True, reason=f"GH-224 ({ISSUES}/224): max_steps='oops' raises ValueError")
def test_garbage_max_steps_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    _write_config_toml('max_steps = "oops"\n')
    cfg = Config.load()
    assert cfg.max_steps == 5  # DEFAULTS["max_steps"]


@pytest.mark.xfail(
    strict=True, reason=f"GH-224 ({ISSUES}/224): temperature='hot' raises ValueError"
)
def test_garbage_temperature_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    _write_config_toml('temperature = "hot"\n')
    cfg = Config.load()
    assert cfg.temperature == pytest.approx(0.2)


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


@pytest.mark.xfail(
    strict=True, reason=f"GH-225 ({ISSUES}/225): read_url max_chars='bad' raises ValueError"
)
def test_read_url_bad_max_chars_returns_error():
    from sk.tools import registry as reg

    out = reg.dispatch_tool("read_url", {"url": "https://example.com", "max_chars": "bad"})
    assert isinstance(out, str) and out.startswith("Error")


@pytest.mark.xfail(
    strict=True, reason=f"GH-225 ({ISSUES}/225): web_search count='bad' raises ValueError"
)
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
    "echo hi > /dev/nvme0n1",
    "echo hi > /dev/mmcblk0",
    "chmod -Rf 777 /",
    "chmod --recursive 777 /",
    "chown -R nobody /",
    "x(){ x|x& };x",
]


@pytest.mark.xfail(
    strict=True, reason=f"GH-220 ({ISSUES}/220): blocklist bypass spellings are allowed"
)
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


# ---------------------------------------------------------------------------
# Memory safety: forget must never mass-delete on blank input (GH-222)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=f"GH-222 ({ISSUES}/222): forget(' ') deletes all memories")
def test_forget_blank_deletes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    store.save_memory("hello world foo")
    store.save_memory("another thing bar")
    out = store.forget_memory(" ")
    assert "0" in out
    assert len(store.recall_memories("x", limit=10)) == 2


@pytest.mark.xfail(strict=True, reason=f"GH-222 ({ISSUES}/222): forget('') deletes all memories")
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


# ---------------------------------------------------------------------------
# Agent tool-cache key must cover content, not just target (GH-226)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True, reason=f"GH-226 ({ISSUES}/226): same path + different content collide"
)
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


class _RedirectFollowingClient:
    """Emulates httpx with follow_redirects=True: blindly follows Location hops."""

    instances = []

    def __init__(self, *a, **k):
        self.hops = []
        _RedirectFollowingClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kw):
        current = url
        for _ in range(4):
            self.hops.append(current)
            if "127.0.0.1" in current or "169.254" in current:
                return _FakeResp(200)
            current = "http://127.0.0.1:11434/private"
        return _FakeResp(200)


@pytest.mark.xfail(strict=True, reason=f"GH-221 ({ISSUES}/221): redirect to private IP is fetched")
def test_read_url_does_not_fetch_redirect_to_private(monkeypatch):
    import httpx

    from sk.tools import web as webmod

    _RedirectFollowingClient.instances.clear()
    real_blocked = webmod._url_blocked

    def _allow_decoy(url):
        if "example.com" in url:
            return None  # public decoy: skip real DNS so the test stays offline
        return real_blocked(url)

    monkeypatch.setattr(webmod, "_url_blocked", _allow_decoy)
    monkeypatch.setattr(httpx, "Client", _RedirectFollowingClient)
    out = webmod.tool_read_url("https://example.com/page")
    hops = _RedirectFollowingClient.instances[-1].hops
    assert not any("127.0.0.1" in h or "169.254" in h for h in hops), hops
    assert isinstance(out, str) and out.startswith("Error")


def test_direct_private_url_stays_blocked():
    """Guard behavior that already holds — lock it in against weakening."""
    from sk.tools import web as webmod

    assert webmod.tool_read_url("http://127.0.0.1:11434/").startswith("Error")
    assert webmod.tool_read_url("http://169.254.169.254/").startswith("Error")


# ---------------------------------------------------------------------------
# CLI UX: clean errors, clamped limits
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True, reason=f"GH-228 ({ISSUES}/228): --cwd missing dir raises FileNotFoundError"
)
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
