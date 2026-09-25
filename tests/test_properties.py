"""Property tests for allowlist + SSRF guards (#35). Would have caught past bug classes.

- metachar bypass (chaining via ; & | > < ` $ ( ) newline)
- find -exec/-delete bypass, git push bypass, full-path binary bypass
- SSRF via localhost aliases, literal private IPs, non-http schemes
DNS is mocked: no network in CI.
"""

import socket

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from sk.tools.read import ALLOWED_BINARIES, ALLOWED_GIT, BLOCKED_CHARS, _check_cmd
from sk.tools.web import _url_blocked


def _clean_dns(monkeypatch):
    """Resolve every hostname to a public IP so only literal-IP rules can block."""

    def fake_getaddrinfo(host, *a, **k):
        return [(socket.AF_INET, None, None, None, ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _private_dns(monkeypatch):
    """Resolve every hostname to a private IP (fail-closed check)."""

    def fake_getaddrinfo(host, *a, **k):
        return [(socket.AF_INET, None, None, None, ("10.9.9.9", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


safe_word = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789_-./"),
    min_size=1,
    max_size=20,
)

DNS_SETTINGS = settings(
    max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture]
)


@given(
    cmd=st.builds(
        lambda a, m, b: f"{a}{m}{b}",
        st.text(
            alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789_-./ "),
            min_size=0,
            max_size=20,
        ),
        st.sampled_from(sorted(BLOCKED_CHARS - {"\n"})),
        st.text(
            alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789_-./ "),
            min_size=0,
            max_size=20,
        ),
    )
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_any_metachar_is_blocked(cmd):
    """Past class: chaining/redirect via a single metachar anywhere."""
    assert isinstance(_check_cmd(cmd), str)


@given(cmd=st.just("echo hi\nbye"))
@settings(max_examples=5)
def test_newline_metachar_is_blocked(cmd):
    assert isinstance(_check_cmd(cmd), str)


@given(binary=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=12))
@settings(max_examples=200)
def test_unknown_binary_blocked(binary):
    assume(binary not in ALLOWED_BINARIES)
    res = _check_cmd(f"{binary} hello")
    assert isinstance(res, str) and "allowlist" in res


@given(binary=st.sampled_from(sorted(ALLOWED_BINARIES - {"git", "find"})))
@settings(max_examples=50)
def test_allowlisted_binary_passes_shape(binary):
    res = _check_cmd(f"{binary} hello")
    assert isinstance(res, tuple) and res[0] == binary


@given(sub=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=10))
@settings(max_examples=200)
def test_git_subcommand_gated(sub):
    assume(sub not in ALLOWED_GIT)
    assert isinstance(_check_cmd(f"git {sub}"), str)


@given(flag=st.sampled_from(["-exec", "-delete"]), extra=safe_word)
@settings(max_examples=100)
def test_find_exec_delete_always_blocked(flag, extra):
    assert isinstance(_check_cmd(f"find /tmp {flag} {extra}"), str)


@given(binary=st.sampled_from(["rm", "sh", "bash", "curl", "wget", "sudo"]))
@settings(max_examples=50)
def test_full_path_does_not_bypass(binary):
    """Past class: /bin/rm must resolve via basename, still blocked."""
    assert isinstance(_check_cmd(f"/bin/{binary} x"), str)
    assert isinstance(_check_cmd(f"/usr/bin/{binary} x"), str)


@given(scheme=st.sampled_from(["ftp", "file", "gopher", "data", "", "ssh"]))
@DNS_SETTINGS
def test_non_http_scheme_blocked(monkeypatch, scheme):
    _clean_dns(monkeypatch)
    assert _url_blocked(f"{scheme}://example.com/x") is not None


@given(
    host=st.sampled_from(
        [
            "localhost",
            "LOCALHOST",
            "x.local",
            "a.b.internal",
            "127.0.0.1",
            "10.0.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "::1",
        ]
    )
)
@DNS_SETTINGS
def test_local_addrs_blocked(monkeypatch, host):
    _clean_dns(monkeypatch)
    assert _url_blocked(f"https://{host}/x") is not None


@given(length=st.integers(min_value=2001, max_value=3000))
@DNS_SETTINGS
def test_overlong_url_blocked(monkeypatch, length):
    _clean_dns(monkeypatch)
    assert _url_blocked("https://example.com/" + "a" * length) is not None


@given(path=st.text(alphabet="abcdefghijklmnopqrstuvwxyz/", min_size=1, max_size=30))
@DNS_SETTINGS
def test_public_https_passes_with_clean_dns(monkeypatch, path):
    _clean_dns(monkeypatch)
    assert _url_blocked(f"https://example.com/{path}") is None


@given(path=st.text(alphabet="abcdefghijklmnopqrstuvwxyz/", min_size=1, max_size=30))
@DNS_SETTINGS
def test_private_dns_is_fail_closed(monkeypatch, path):
    _private_dns(monkeypatch)
    assert _url_blocked(f"https://example.com/{path}") is not None
