"""MCP hardening: subprocess env allowlist, URL validation, trust tier (#302, #303).

Remote MCP servers are arbitrary code that receives whatever the client sends.
Three gaps let one compromise far more than it should:

- a stdio server inherited the *entire* parent environment, including every
  provider API key and cloud token the user had in their shell;
- a remote URL was validated with `re.match(r"https?://", url)`, which accepts
  userinfo, fragments, and plain HTTP to anywhere;
- configured headers rode along across a redirect, so a 302 to an attacker host
  replayed the Authorization header.
"""

import pytest

from sk.config import _normalize_trust, _parse_inherit_env, _validate_mcp_url
from sk.mcp_client import _read_only_hint, _safe_env, _same_origin, trust_gate_error


# --- #302: environment allowlist --------------------------------------------


def test_secrets_not_inherited_by_default(monkeypatch):
    monkeypatch.setenv("SIDEKICK_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "also-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_leak")
    env = _safe_env({})
    for name in ("SIDEKICK_API_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN"):
        assert name not in env, name


def test_basics_are_inherited(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/x")
    monkeypatch.setenv("XDG_DATA_HOME", "/home/x/.local/share")
    env = _safe_env({})
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/x"
    assert env["XDG_DATA_HOME"] == "/home/x/.local/share"


def test_explicit_env_always_applied():
    assert _safe_env({"MY_TOKEN": "t"}).get("MY_TOKEN") == "t"


def test_inherit_env_is_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "nope")
    env = _safe_env({}, ("AWS_REGION",))
    assert env.get("AWS_REGION") == "eu-west-1"
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_inherit_env_glob(monkeypatch):
    monkeypatch.setenv("FOO_A", "1")
    monkeypatch.setenv("BAR_A", "2")
    env = _safe_env({}, ("FOO_*",))
    assert env.get("FOO_A") == "1"
    assert "BAR_A" not in env


def test_inherit_env_parsed():
    assert _parse_inherit_env(["A", " B ", ""]) == ("A", "B")
    assert _parse_inherit_env("nope") == ()
    assert _parse_inherit_env(None) == ()


# --- #303: URL validation ----------------------------------------------------

ACCEPTED = [
    "https://mcp.example.com/mcp",
    "http://localhost:8000/mcp",
    "http://127.0.0.1/mcp",
    "http://[::1]:9000/mcp",
    "https://a.example:443/x?y=1",
]
REJECTED = [
    "",
    "ftp://x/mcp",
    "file:///etc/passwd",
    "gopher://x",
    "https://user:pass@mcp.example.com/mcp",  # authority-parsing differential
    "https://mcp.example.com/mcp#frag",  # fragment never reaches the server
    "http://mcp.example.com/mcp",  # cleartext off-host
    "http://10.0.0.5/mcp",
    "http://100.100.100.200/latest/meta-data/",  # cloud metadata
    "https:///mcp",
    "http://localhost.evil.com/mcp",  # suffix confusion
    "not a url",
]


@pytest.mark.parametrize("u", ACCEPTED)
def test_url_accepted(u):
    assert _validate_mcp_url(u) is True, u


@pytest.mark.parametrize("u", REJECTED)
def test_url_rejected(u):
    assert _validate_mcp_url(u) is False, u


def test_same_origin():
    assert _same_origin("https://a.example/mcp", "https://a.example/x") is True
    assert _same_origin("https://a.example/mcp", "https://evil.example/x") is False
    assert _same_origin("https://a.example/mcp", "http://a.example/x") is False
    assert _same_origin("http://localhost:8000/m", "http://localhost:9000/m") is False


# --- #303: trust tier --------------------------------------------------------


def test_trust_defaults_to_untrusted():
    assert _normalize_trust(None) == "untrusted"
    assert _normalize_trust("") == "untrusted"
    assert _normalize_trust("banana") == "untrusted"
    assert _normalize_trust("UNTRUSTED") == "untrusted"
    assert _normalize_trust("full") == "full"
    assert _normalize_trust(" FULL ") == "full"


READ_ONLY = [{"name": "read", "annotations": {"readOnlyHint": True}}]
WRITE_CAPABLE = [
    {"name": "deploy"},
    {"name": "y", "annotations": None},
    {"name": "z", "annotations": {"readOnlyHint": False}},
    {"name": "s", "annotations": {"readOnlyHint": "yes"}},  # must be literal True
]


def test_untrusted_read_only_passes():
    spec = {"name": "srv", "trust": "untrusted"}
    assert trust_gate_error(spec, "read", READ_ONLY) is None


@pytest.mark.parametrize("tool", ["deploy", "y", "z", "s"])
def test_untrusted_write_capable_blocked(tool):
    spec = {"name": "srv", "trust": "untrusted"}
    err = trust_gate_error(spec, tool, WRITE_CAPABLE)
    assert err and "untrusted" in err


def test_full_trust_allows():
    spec = {"name": "srv", "trust": "full"}
    assert trust_gate_error(spec, "deploy", WRITE_CAPABLE) is None


def test_missing_trust_key_is_untrusted():
    """Fail closed: an unclassified server is not trusted."""
    err = trust_gate_error({"name": "srv"}, "deploy", WRITE_CAPABLE)
    assert err and "untrusted" in err


def test_read_only_hint_requires_literal_true():
    assert _read_only_hint({"annotations": {"readOnlyHint": True}}) is True
    for bad in (
        {"annotations": {"readOnlyHint": "true"}},
        {"annotations": {"readOnlyHint": 1}},
        {"annotations": {}},
        {"annotations": None},
        {},
        None,
    ):
        assert _read_only_hint(bad) is False
