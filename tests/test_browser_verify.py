"""The optional browser verification pack: no dependency, gated, testable (#520)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import sk.browser_verify as bv
from sk.cli import app

_REAL_DETECT = bv.detect
PW = bv.Browser("playwright", "/usr/bin/python")
CHROME = bv.Browser("chrome", "/usr/bin/chromium")


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch):
    monkeypatch.setattr(bv, "detect", lambda: None)
    yield


def test_detect_reports_unavailable_without_installing(monkeypatch):
    import importlib.util
    import shutil

    monkeypatch.setattr(bv, "detect", _REAL_DETECT)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert bv.detect() is None


def test_no_browser_means_no_launch_and_no_failure():
    calls = []
    result = bv.verify(
        "https://example.com",
        runner=lambda argv: calls.append(argv) or (0, "", ""),
    )
    assert result["available"] is False and result["ok"] is False
    assert "No browser" in result["note"] and calls == []


def test_a_blocked_url_never_launches_a_browser(monkeypatch):
    monkeypatch.setattr(bv, "detect", lambda: PW)
    calls = []
    result = bv.verify(
        "https://127.0.0.1:9/",
        runner=lambda argv: calls.append(argv) or (0, "", ""),
    )
    assert calls == [], "a blocked URL reached a browser"
    assert result["ok"] is False and ("blocked" in result["note"] or "egress" in result["note"])


def test_check_target_uses_egress_and_ssrf(monkeypatch):
    assert "egress blocked" in bv.check_target("https://example.com/")
    import sk.egress as egress

    monkeypatch.setattr(egress, "check", lambda url: "")
    assert "blocked" in bv.check_target("http://127.0.0.1:9/")


def test_playwright_command_carries_every_artifact_path():
    argv = bv.build_command(
        "https://example.com",
        snapshot="/tmp/s.txt",
        screenshot="/tmp/s.png",
        emit_test="/tmp/test.py",
        browser=PW,
    )
    assert argv[:3] == ["/usr/bin/python", "-m", "sk.browser_probe"]
    assert argv[3] == "https://example.com"
    assert argv[argv.index("--snapshot") + 1] == "/tmp/s.txt"
    assert argv[argv.index("--emit-test") + 1] == "/tmp/test.py"


def test_chrome_command_dumps_dom_and_screenshots():
    argv = bv.build_command("https://example.com", screenshot="/tmp/s.png", browser=CHROME)
    assert argv[0] == "/usr/bin/chromium"
    assert "--headless=new" in argv and "--dump-dom" in argv
    assert "--screenshot=/tmp/s.png" in argv


def test_a_successful_run_writes_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(bv, "detect", lambda: PW)
    monkeypatch.setattr(bv, "check_target", lambda url: "")
    snap = tmp_path / "snap.txt"
    test = tmp_path / "test.py"
    result = bv.verify(
        "https://example.com",
        snapshot=str(snap),
        emit_test=str(test),
        runner=lambda argv: (0, "accessibility tree", ""),
    )
    assert result["ok"] is True and result["snapshot"] == str(snap)
    assert "accessibility tree" in snap.read_text()
    assert "page.goto('https://example.com')" in test.read_text()
    assert result["test"] == str(test)


def test_emitted_test_is_valid_python():
    compile(bv.generate_test("https://example.com"), "generated.py", "exec")


def test_a_browser_error_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(bv, "detect", lambda: PW)
    monkeypatch.setattr(bv, "check_target", lambda url: "")
    result = bv.verify(
        "https://example.com",
        runner=lambda argv: (_ for _ in ()).throw(OSError("no display")),
    )
    assert result["ok"] is False and "no display" in result["note"]


def test_cli_unavailable_is_clear_and_succeeds():
    result = CliRunner().invoke(app, ["verify-browser", "https://example.com"])
    assert result.exit_code == 0, result.output
    assert "No browser" in result.output


def test_cli_json_shape():
    result = CliRunner().invoke(app, ["verify-browser", "https://example.com", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["schema"] == 1 and data["available"] is False
