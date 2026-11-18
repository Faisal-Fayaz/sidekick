"""Opt-in crash telemetry: off by default, redacted, best-effort (#507)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import sk.telemetry as tel


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from sk import config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.toml")
    for key in ("SIDEKICK_TELEMETRY", "SIDEKICK_TELEMETRY_ENDPOINT"):
        monkeypatch.delenv(key, raising=False)
    yield tmp_path


def test_disabled_by_default_writes_nothing(isolated):
    assert tel.enabled() is False
    assert tel.report(RuntimeError("boom")) is False
    assert not (isolated / tel.TELEMETRY_FILE).exists()


def test_env_opt_in_writes_a_redacted_line(isolated, monkeypatch):
    monkeypatch.setenv("SIDEKICK_TELEMETRY", "1")
    assert tel.enabled() is True
    assert tel.report(ValueError("bad key sk-or-v1-secretvalue")) is True
    lines = (isolated / tel.TELEMETRY_FILE).read_text().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["schema"] == 1
    assert data["type"] == "ValueError"
    assert "<redacted-key>" in data["message"]
    assert "secretvalue" not in data["message"]


def test_global_config_can_opt_in_but_a_project_file_cannot(isolated):
    (isolated / "config.toml").write_text(
        'telemetry = true\ntelemetry_endpoint = "https://collector.test/in"\n'
    )
    assert tel.enabled() is True
    assert tel.endpoint() == "https://collector.test/in"
    (isolated / "config.toml").unlink()
    (isolated / ".sidekick.toml").write_text(
        "telemetry = true\ntelemetry_endpoint = 'https://evil.test'\n"
    )
    assert tel.enabled() is False
    assert tel.endpoint() == ""


def test_redaction_covers_secrets_paths_urls_and_emails():
    text = (
        "token=abc123 Bearer xyz789 sk-or-v1-abcdef "
        "password=hunter2 alice@example.com "
        "https://evil.test/x?k=1 /home/faisal/.ssh/id_rsa"
    )
    out = tel.redact(text)
    for secret in (
        "abc123",
        "xyz789",
        "abcdef",
        "hunter2",
        "alice@example.com",
        "evil.test",
        "id_rsa",
    ):
        assert secret not in out
    assert "<path>" in out and "<url>" in out and "<email>" in out


def test_payload_has_no_traceback_locals_or_arguments():
    data = tel.payload(RuntimeError("simple failure"), surface="tui")
    assert set(data) == {"schema", "type", "message", "version", "python", "os", "surface"}
    assert data["surface"] == "tui"
    assert "traceback" not in data and "tb" not in data


def test_a_remote_endpoint_is_used_only_when_configured(isolated, monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout=3: calls.append(request) or Response()
    )
    monkeypatch.setenv("SIDEKICK_TELEMETRY", "1")
    tel.report(RuntimeError("x"))
    assert calls == [], "a request was made without an explicit endpoint"

    monkeypatch.setenv("SIDEKICK_TELEMETRY_ENDPOINT", "https://collector.test/in")
    tel.report(RuntimeError("x"))
    assert len(calls) == 1
    assert calls[0].get_method() == "POST"


def test_telemetry_failures_never_raise(isolated, monkeypatch):
    monkeypatch.setenv("SIDEKICK_TELEMETRY", "1")
    monkeypatch.setenv("SIDEKICK_TELEMETRY_ENDPOINT", "https://collector.test/in")
    # CONFIG_DIR points at a regular file, so opening the sink fails; the
    # remote call fails too. Neither may escape or change the return contract.
    blocker = isolated / "not-a-dir"
    blocker.write_text("x")
    from sk import config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", blocker)
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
    )
    assert tel.report(RuntimeError("x")) is False


def test_excepthook_delegates_and_only_reports_when_enabled(isolated, monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: seen.append(a))
    monkeypatch.delattr(sys, "_sk_telemetry_hook", raising=False)
    monkeypatch.setattr(tel, "report", lambda exc, **kw: seen.append("reported") or True)

    tel.install_excepthook()
    tel.install_excepthook()  # idempotent
    error = RuntimeError("boom")
    sys.excepthook(RuntimeError, error, None)
    assert seen[-1] == (RuntimeError, error, None), "the original traceback was swallowed"
    assert "reported" not in seen, "disabled telemetry still reported"

    monkeypatch.setenv("SIDEKICK_TELEMETRY", "1")
    sys.excepthook(RuntimeError, error, None)
    assert "reported" in seen
