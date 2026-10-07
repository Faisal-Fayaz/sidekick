"""Credential storage paths (#370).

`keyring.py` was at 56% coverage, and the *only* test stubbed the backend away
entirely -- so every function returned at its first guard and none of the code
that actually stores a secret ever ran:

- `secret-tool store` with the secret on **stdin, never argv**. That is the
  module's own stated security property (argv is visible in `ps`), asserted
  nowhere.
- the macOS **delete-then-add** overwrite. `security` has no overwrite, so
  `set_key` deletes before adding; a regression silently drops a user's API key
  and nothing notices until auth fails.
- `security` exit code 44 meaning "the item never existed", which is still
  "gone" -- a real branch that read like a typo.

These fake the subprocess boundary and assert on argv and stdin, so the property
being tested is that the secret does not appear in the process table.
"""

from __future__ import annotations

import subprocess

import pytest

import sk.keyring as kr


class _Run:
    """Records every subprocess.run call; replays queued results."""

    def __init__(self):
        self.calls: list[dict] = []
        self.results: list[object] = []

    def __call__(self, argv, **kw):
        self.calls.append({"argv": list(argv), **kw})
        if self.results:
            r = self.results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture()
def fake_runs(monkeypatch):
    r = _Run()
    monkeypatch.setattr(kr.subprocess, "run", r)
    return r


def _secret_tool(monkeypatch):
    monkeypatch.setattr(kr.shutil, "which", lambda *a, **kw: "/usr/bin/secret-tool")


def _security(monkeypatch):
    # `backend()` checks secret-tool first, so it has to be absent for the macOS
    # branch to be reachable at all.
    monkeypatch.setattr(
        kr.shutil,
        "which",
        lambda n, *a, **kw: None if n == "secret-tool" else "/usr/bin/security",
    )


def _argv_text(call):
    return " ".join(str(a) for a in call["argv"])


# --- backend detection ------------------------------------------------------


def test_backend_prefers_secret_tool(monkeypatch):
    monkeypatch.setattr(kr.shutil, "which", lambda n: f"/usr/bin/{n}")
    assert kr.backend() == "secret-tool"


def test_backend_falls_back_to_security(monkeypatch):
    monkeypatch.setattr(kr.shutil, "which", lambda n: None if n == "secret-tool" else "/usr/bin/x")
    assert kr.backend() == "security"


def test_backend_empty_when_neither(monkeypatch):
    monkeypatch.setattr(kr.shutil, "which", lambda *a, **kw: None)
    assert kr.backend() == ""


# --- the security property: secrets never touch argv -----------------------


def test_set_key_secret_travels_on_stdin_not_argv(monkeypatch, fake_runs):
    """`ps` shows every process's argv, so a secret passed as an argument leaks
    to every user on the machine for the lifetime of the call."""
    _secret_tool(monkeypatch)
    assert kr.set_key("openai", "sk-super-secret-value") is True
    call = fake_runs.calls[0]
    assert call["input"] == "sk-super-secret-value"
    assert "sk-super-secret-value" not in _argv_text(call)


def test_get_key_never_passes_a_secret_because_it_only_reads(monkeypatch, fake_runs):
    _secret_tool(monkeypatch)
    fake_runs.results = [subprocess.CompletedProcess([], 0, "sk-stored\n", "")]
    assert kr.get_key("openai") == "sk-stored"
    assert "-w" not in fake_runs.calls[0]["argv"]


def test_delete_key_secret_travels_on_stdin_not_argv(monkeypatch, fake_runs):
    _security(monkeypatch)
    assert kr.delete_key("openai") is True
    assert "sk-" not in _argv_text(fake_runs.calls[0])


# --- secret-tool paths ------------------------------------------------------


def test_get_key_returns_empty_when_absent(monkeypatch, fake_runs):
    _secret_tool(monkeypatch)
    fake_runs.results = [subprocess.CompletedProcess([], 1, "", "no such item")]
    assert kr.get_key("openai") == ""


def test_set_key_strips_the_secret(monkeypatch, fake_runs):
    _secret_tool(monkeypatch)
    kr.set_key("openai", "  sk-padded  ")
    assert fake_runs.calls[0]["input"] == "sk-padded"


def test_set_key_labels_the_item(monkeypatch, fake_runs):
    """The label is what a user sees in their keyring UI; it must name the service."""
    _secret_tool(monkeypatch)
    kr.set_key("openai", "sk-x")
    assert f"{kr.SERVICE} openai" in fake_runs.calls[0]["argv"]


def test_set_key_false_on_backend_failure(monkeypatch, fake_runs):
    _secret_tool(monkeypatch)
    fake_runs.results = [subprocess.CompletedProcess([], 1, "", "denied")]
    assert kr.set_key("openai", "sk-x") is False


def test_delete_key_false_on_backend_failure(monkeypatch, fake_runs):
    _secret_tool(monkeypatch)
    fake_runs.results = [subprocess.CompletedProcess([], 1, "", "boom")]
    assert kr.delete_key("openai") is False


# --- the macOS delete-then-add overwrite -----------------------------------


def test_macos_set_key_deletes_before_adding(monkeypatch, fake_runs):
    """`security add-generic-password` fails if the item exists, so the delete is
    load-bearing. Without it, re-storing a key would silently keep the old one --
    or fail, and the user would never learn which."""
    _security(monkeypatch)
    assert kr.set_key("openai", "sk-second") is True
    tools = [_argv_text(c).split()[0] for c in fake_runs.calls]
    assert tools == ["security", "security"]
    assert "delete-generic-password" in fake_runs.calls[0]["argv"]
    assert "add-generic-password" in fake_runs.calls[1]["argv"]


def test_macos_set_key_false_if_the_add_fails_after_a_successful_delete(monkeypatch, fake_runs):
    """The key is now gone and the new one was not stored -- the worst case, so
    it must report False rather than True."""
    _security(monkeypatch)
    fake_runs.results = [
        subprocess.CompletedProcess([], 0, "", ""),  # delete ok
        subprocess.CompletedProcess([], 1, "", "duplicate"),  # add failed
    ]
    assert kr.set_key("openai", "sk-x") is False


def test_macos_delete_treats_44_as_already_gone(monkeypatch, fake_runs):
    """security exits 44 when the item never existed, which is still "gone"."""
    _security(monkeypatch)
    fake_runs.results = [subprocess.CompletedProcess([], 44, "", "not found")]
    assert kr.delete_key("openai") is True


def test_macos_delete_false_on_other_failures(monkeypatch, fake_runs):
    _security(monkeypatch)
    fake_runs.results = [subprocess.CompletedProcess([], 1, "", "locked")]
    assert kr.delete_key("openai") is False


# --- guards: everything degrades, nothing raises ---------------------------


def test_no_backend_makes_every_call_a_noop(monkeypatch):
    monkeypatch.setattr(kr.shutil, "which", lambda *a, **kw: None)
    assert kr.get_key("openai") == ""
    assert kr.set_key("openai", "sk-x") is False
    assert kr.delete_key("openai") is False


@pytest.mark.parametrize("provider", ["", "   ", None])
def test_blank_provider_is_refused(monkeypatch, fake_runs, provider):
    _secret_tool(monkeypatch)
    assert kr.get_key(provider) == ""
    assert kr.set_key(provider, "sk-x") is False
    assert kr.delete_key(provider) is False
    assert fake_runs.calls == [], "must not reach the keyring at all"


@pytest.mark.parametrize("secret", ["", "   ", None])
def test_blank_secret_is_refused(monkeypatch, fake_runs, secret):
    _secret_tool(monkeypatch)
    assert kr.set_key("openai", secret) is False
    assert fake_runs.calls == []


def test_subprocess_timeout_never_escapes(monkeypatch, fake_runs):
    """A wedged keyring daemon must not hang auth or crash the CLI."""
    _secret_tool(monkeypatch)
    fake_runs.results = [subprocess.TimeoutExpired(cmd="secret-tool", timeout=10)]
    assert kr.get_key("openai") == ""
    fake_runs.results = [subprocess.TimeoutExpired(cmd="secret-tool", timeout=15)]
    assert kr.set_key("openai", "sk-x") is False
    fake_runs.results = [subprocess.TimeoutExpired(cmd="secret-tool", timeout=10)]
    assert kr.delete_key("openai") is False


def test_oserror_never_escapes(monkeypatch, fake_runs):
    _secret_tool(monkeypatch)
    fake_runs.results = [OSError("no dbus")]
    assert kr.get_key("openai") == ""
    fake_runs.results = [PermissionError("denied")]
    assert kr.delete_key("openai") is False


def test_timeouts_are_bounded(monkeypatch, fake_runs):
    """Every call carries a timeout, so a hung backend cannot block forever."""
    _secret_tool(monkeypatch)
    kr.get_key("openai")
    kr.set_key("openai", "sk-x")
    kr.delete_key("openai")
    assert all("timeout" in c for c in fake_runs.calls)
