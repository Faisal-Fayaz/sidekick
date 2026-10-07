"""Egress policy (#328): the model may only cause fetches to allowlisted hosts.

Deny by default is the load-bearing behaviour, so most of these run with an
explicit allowlist argument rather than relying on ambient config — the parsing
and matching rules are tested directly, and the enforcement points are tested
through the real tool entry points.
"""

from __future__ import annotations

import socket

import sk.config as config_mod
import sk.store as store
from sk.egress import REASON_BAD_URL, REASON_NO_ALLOWLIST, check, host_allowed, hint

# --- host matching ----------------------------------------------------------


def test_exact_host_match():
    assert host_allowed("example.com", ("example.com",))
    assert host_allowed("EXAMPLE.com.", ("example.com",))
    assert not host_allowed("sub.example.com", ("example.com",))


def test_wildcard_matches_subdomains_only():
    allow = ("*.example.com",)
    assert host_allowed("a.example.com", allow)
    assert host_allowed("deep.nested.example.com", allow)
    assert not host_allowed("example.com", allow)  # the bare domain is not covered
    assert not host_allowed("notexample.com", allow)


def test_bare_wildcard_is_never_honoured():
    """A `*` in a hand-edited config must not silently disable the control."""
    assert not host_allowed("evil.example", ("*",))
    assert not host_allowed("evil.example", ("**",))
    assert not host_allowed("anything.test", ("*", "also.example"))


def test_empty_allowlist_allows_nothing():
    assert not host_allowed("example.com", ())
    assert not host_allowed("", ("example.com",))


# --- config parsing ---------------------------------------------------------


def test_parse_allowlist_normalises():
    assert config_mod._parse_egress_allow(["Example.COM", " *.GitHub.com "]) == (
        "example.com",
        "*.github.com",
    )


def test_parse_allowlist_refuses_wildcards_and_junk():
    assert config_mod._parse_egress_allow(["*", "**", "0.0.0.0/0", "ok.example"]) == ("ok.example",)
    assert config_mod._parse_egress_allow("not-a-list") == ()
    assert config_mod._parse_egress_allow(None) == ()
    assert config_mod._parse_egress_allow(["", "  ", "a.com", "a.com"]) == ("a.com",)


def test_project_file_cannot_widen_egress():
    """A hostile repo must not be able to grant itself network access (#299)."""
    assert "egress_allow" in config_mod.PROJECT_BLOCKED_KEYS


# --- check() ----------------------------------------------------------------


def test_check_denies_by_default():
    assert check("https://example.com/x", ()) == REASON_NO_ALLOWLIST


def test_check_allows_and_denies():
    allow = ("example.com",)
    assert check("https://example.com/x", allow) is None
    assert "not in the egress allowlist" in str(check("https://evil.test/x", allow))


def test_check_rejects_unparseable_url():
    assert check("not-a-url", ("example.com",)) == REASON_BAD_URL


def test_hint_names_the_host_not_the_url():
    assert hint("https://example.com/deep/path?q=1") == "sk egress allow example.com"
    assert hint("Example.COM") == "sk egress allow example.com"
    assert hint("") == "sk egress allow <host>"


# --- enforcement at the fetch chokepoint ------------------------------------


class _Resp:
    def __init__(self, status=200, location=None, text=""):
        self.status_code = status
        self.headers = {"content-type": "text/html"}
        if location:
            self.headers["location"] = location
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _Chain:
    """Records every hop so a test can assert what was never fetched."""

    mode = "ok"
    hops: list[str] = []

    def __init__(self, *a, **k):
        assert k.get("follow_redirects") is False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kw):
        _Chain.hops.append(url)
        if _Chain.mode == "to-internal":
            if url == "https://ok.example/start":
                return _Resp(302, location="http://169.254.169.254/latest/meta-data/")
            raise AssertionError(f"must never fetch {url}")
        if _Chain.mode == "to-unlisted":
            if url == "https://ok.example/start":
                return _Resp(302, location="https://evil.test/payload")
            raise AssertionError(f"must never fetch {url}")
        if _Chain.mode == "chain-to-unlisted":
            # allowlisted -> allowlisted -> unlisted: the third hop is the only
            # one the policy can catch, so the chain has to be walked to prove
            # the check is not short-circuited after the first hop.
            if url == "https://ok.example/start":
                return _Resp(302, location="https://news.example/mid")
            if url == "https://news.example/mid":
                return _Resp(302, location="https://evil.test/payload")
            raise AssertionError(f"must never fetch {url}")
        return _Resp(200, text="<html><body>hello</body></html>")


def _patch(monkeypatch, mode="ok", allow=()):
    """Exercise the real enforcement path: only DNS and HTTP are stubbed.

    The allowlist is injected by overriding Config.load, so `_fetch_with_redirects`
    runs its genuine gate logic rather than a test-local reimplementation of it.

    DNS is stubbed at `getaddrinfo`, not by faking `_url_blocked`. Faking the
    guard let these tests pass for the wrong reason: with only `ok.example`
    exempted, a redirect to `evil.test` hit the real guard, failed to resolve,
    and returned "DNS failed" — so `test_redirect_to_unlisted_host_is_refused`
    asserted a DNS failure while appearing to prove the allowlist was enforced
    on redirect hops. It was not, and the bypass was live. Making both hosts
    resolve to a public address leaves the allowlist as the only thing that can
    refuse the hop, which is the property worth testing. Literal private IPs
    are still caught, because `_blocked_ip` fires on the literal before DNS is
    consulted at all.
    """
    from sk.tools import web as webmod

    _Chain.hops = []
    _Chain.mode = mode
    public_ip = "93.184.216.34"
    resolvable = {"ok.example", "evil.test", "other.example", "news.example"}

    real_getaddrinfo = socket.getaddrinfo

    def _fake_dns(host, *a, **kw):
        if str(host) in resolvable:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (public_ip, 0))]
        return real_getaddrinfo(host, *a, **kw)

    real_load = config_mod.Config.load

    def _load(*a, **kw):
        cfg = real_load(*a, **kw)
        cfg.egress_allow = tuple(allow)
        return cfg

    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns)
    monkeypatch.setattr(config_mod.Config, "load", staticmethod(_load))
    monkeypatch.setattr("httpx.Client", _Chain)


def test_fetch_blocked_when_allowlist_empty(monkeypatch):
    from sk.tools.web import tool_read_url

    _patch(monkeypatch, allow=())
    out = tool_read_url("https://ok.example/page")
    assert "egress blocked" in out
    assert _Chain.hops == []  # nothing left the process


def test_fetch_allowed_when_allowlisted(monkeypatch):
    from sk.tools.web import tool_read_url

    _patch(monkeypatch, allow=("ok.example",))
    out = tool_read_url("https://ok.example/page")
    assert "hello" in out


def test_redirect_to_unlisted_host_is_refused(monkeypatch):
    """The bypass that matters: allowlist the first hop, redirect elsewhere.

    Both hosts resolve publicly, so the SSRF guard passes for both and the
    egress allowlist is the only thing that can refuse the hop. Before the fix
    `evil.test` was fetched and its content returned to the model.
    """
    from sk.tools.web import tool_read_url

    _patch(monkeypatch, mode="to-unlisted", allow=("ok.example",))
    out = tool_read_url("https://ok.example/start")
    assert "egress blocked" in out
    assert "evil.test" not in _Chain.hops  # nothing left the process
    # The refusal names the host and the remediation, because that is what the
    # user needs in order to decide; `_Chain.hops` is the assertion that matters.
    assert "sk egress allow evil.test" in out


def test_redirect_to_unlisted_host_is_recorded_as_denied(monkeypatch):
    """A refused hop must leave an audit row naming the target it refused.

    Otherwise `sk audit --prove` cannot distinguish "the policy held" from
    "we never tried", which is the whole evidentiary point of the ledger.
    """
    from sk.tools.web import tool_read_url

    _patch(monkeypatch, mode="to-unlisted", allow=("ok.example",))
    tool_read_url("https://ok.example/start")
    rows = {r["host"]: r for r in store.egress_summary()}
    assert "evil.test" in rows, "the denied redirect left no ledger row"
    assert rows["evil.test"]["denied"] == 1
    assert rows["evil.test"]["allowed"] == 0


def test_multi_hop_chain_stops_at_the_first_denied_hop(monkeypatch):
    """The allowlist gates every hop, not just the first: allowlisted ->
    allowlisted -> unlisted must still refuse, and must not fetch hop 3."""
    from sk.tools.web import tool_read_url

    _patch(monkeypatch, mode="chain-to-unlisted", allow=("ok.example", "news.example"))
    out = tool_read_url("https://ok.example/start")
    assert "egress blocked" in out
    assert "evil.test" not in _Chain.hops
    # The first hop was allowed, so it was genuinely fetched.
    assert "https://ok.example/start" in _Chain.hops


def test_redirect_to_internal_is_refused(monkeypatch):
    """SSRF still guards allowlisted hosts: a link-local literal is refused."""
    from sk.tools.web import tool_read_url

    _patch(monkeypatch, mode="to-internal", allow=("ok.example",))
    out = tool_read_url("https://ok.example/start")
    assert "blocked" in out
    assert "169.254.169.254" not in _Chain.hops


def test_denial_message_is_actionable(monkeypatch):
    from sk.tools.web import tool_read_url

    _patch(monkeypatch, allow=())
    out = tool_read_url("https://ok.example/page")
    assert "sk egress allow ok.example" in out


# --- ledger -----------------------------------------------------------------


def test_ledger_records_allow_and_deny(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "h.db")
    store.log_egress("read_url", "ok.example", "https://ok.example/a", None)
    store.log_egress("read_url", "evil.test", "https://evil.test/b", "not in the allowlist")
    rows = {r["host"]: r for r in store.egress_summary()}
    assert rows["ok.example"]["allowed"] == 1
    assert rows["evil.test"]["denied"] == 1
    assert "not in the allowlist" in rows["evil.test"]["reasons"][0]


def test_ledger_redacts_secrets_in_url(tmp_path, monkeypatch):
    """A blocked URL may carry a token; the ledger must not keep it (#299)."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "h.db")
    secret = "sk-secret-value-1234"
    store.log_egress("read_url", "ok.example", f"https://ok.example/a?api_key={secret}", None)
    conn = store._connect()
    try:
        rows = conn.execute("SELECT target FROM tool_runs WHERE tool LIKE 'egress:%'").fetchall()
    finally:
        conn.close()
    assert rows and all(secret not in str(r[0]) for r in rows)


def test_egress_summary_never_raises_on_broken_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "nope" / "missing.db")
    assert store.egress_summary() == []


# --- CLI --------------------------------------------------------------------


def _cli():
    from typer.testing import CliRunner

    from sk.cli import app

    return CliRunner(), app


def test_cli_list_warns_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    runner, app = _cli()
    out = runner.invoke(app, ["egress", "list"]).output
    assert "EMPTY" in out
    assert "blocked" in out.lower()


def test_cli_allow_deny_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    runner, app = _cli()
    assert (
        "allowed example.com"
        in runner.invoke(app, ["egress", "allow", "https://Example.com/x"]).output
    )
    assert "example.com" in config_mod.Config.load().egress_allow
    assert "denied example.com" in runner.invoke(app, ["egress", "deny", "example.com"]).output
    assert "example.com" not in config_mod.Config.load().egress_allow


def test_cli_refuses_bare_wildcard(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    runner, app = _cli()
    res = runner.invoke(app, ["egress", "allow", "*"])
    assert res.exit_code == 1
    assert "Refusing" in res.output
    assert config_mod.Config.load().egress_allow == ()


def test_cli_test_verb(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    runner, app = _cli()
    runner.invoke(app, ["egress", "allow", "example.com"])
    assert "allowed" in runner.invoke(app, ["egress", "test", "https://example.com/x"]).output
    assert "blocked" in runner.invoke(app, ["egress", "test", "https://evil.test/x"]).output


def test_cli_unknown_action_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    runner, app = _cli()
    assert runner.invoke(app, ["egress", "frobnicate"]).exit_code == 1


# --- config persistence -----------------------------------------------------


def test_save_round_trips_allowlist(tmp_path, monkeypatch):
    """`sk config` must not wipe the allowlist written by `sk egress allow`."""
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = config_mod.Config.load()
    cfg.egress_allow = ("example.com", "*.github.com")
    cfg.save()
    assert config_mod.Config.load().egress_allow == ("example.com", "*.github.com")


# --- step-budget synthesis must not leak reasoning (#328-era agent path) ----


class _FakeMsg:
    def __init__(self, content="", reasoning=""):
        self.content = content
        self.reasoning = reasoning
        self.tool_calls = None
        self.finish_reason = "stop"


def test_exhaustion_report_never_includes_reasoning(monkeypatch):
    """The 3-line budget report leaked raw chain-of-thought into the transcript.

    A real turn came back with the three requested lines *plus* the model
    narrating its own instructions ("We need must exactly 3 lines. … No tools
    now."), because the synthesis appended `msg.reasoning` to the visible text.
    The normal path already had the right invariant stated in a comment —
    "never post it as chat" — so this was the single place violating it.
    """
    from sk import agent as agent_mod

    reasoning = (
        "We need must exactly 3 lines. Accomplished grounded tool. "
        "Blocked exec plan mode. Next maybe ask switch? No tools now."
    )
    monkeypatch.setattr(
        agent_mod,
        "_stream_chat",
        lambda *a, **kw: _FakeMsg(
            content="Accomplished: a\nBlocked: b\nNext: c", reasoning=reasoning
        ),
    )
    out = agent_mod._synthesize_exhaustion(
        None, "m", [{"role": "user", "content": "x"}], 0.2, 400, {}, None
    )
    assert out == "Accomplished: a\nBlocked: b\nNext: c"
    assert "exactly 3 lines" not in out
    assert "No tools now" not in out


def test_exhaustion_report_survives_a_reasoning_only_model(monkeypatch):
    """Dropping reasoning must not blank the report when content is empty.

    The old concatenation happened to return *something* for a reasoning-only
    response. An empty report falls back to "(max steps reached)", which is
    honest, but the reasoning must still never be shown.
    """
    from sk import agent as agent_mod

    monkeypatch.setattr(
        agent_mod,
        "_stream_chat",
        lambda *a, **kw: _FakeMsg(content="", reasoning="secret thoughts"),
    )
    out = agent_mod._synthesize_exhaustion(None, "m", [], 0.2, 400, {}, None)
    assert "secret thoughts" not in out
