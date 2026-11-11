"""CLI wiring for `sk check`: trust gate, reports, exit codes, approvals."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sk.check.config import CheckCommand, CheckPack
from sk.check.parsers import Diagnostic
from sk.check.runner import CheckReport, CheckResult
from sk.cli import app
from sk.config import Config


def _cfg():
    return Config(provider="ollama", model="fake", base_url="http://x/v1", api_key="x")


def _pack(*checks: CheckCommand, trusted: bool = True) -> CheckPack:
    return CheckPack(
        root="/tmp/repo",
        present=True,
        trusted=trusted,
        checks=checks if trusted else (),
        declared=checks,
    )


def _write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".sidekick" / "checks.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _result(status: str = "pass", *, note: str = "", raw: str = "") -> CheckResult:
    return CheckResult(
        id="lint",
        command="ruff check .",
        kind="lint",
        parser="ruff",
        status=status,
        exit_code=0 if status == "pass" else 1,
        duration=0.01,
        diagnostics=(Diagnostic("src/a.py", 1, 2, "error", "F401", "unused", "ruff", "lint"),)
        if status == "fail"
        else (),
        parsed=status != "unknown",
        raw=raw,
        note=note,
    )


def _patch_runner(monkeypatch, report: CheckReport | None = None, *, seen: dict | None = None):
    import sk.check

    pack = _pack(CheckCommand(id="lint", command="ruff check .", parser="ruff"))

    def fake_load(**kwargs):
        if seen is not None:
            seen["load"] = kwargs
        return pack

    def fake_run(**kwargs):
        if seen is not None:
            seen["run"] = kwargs
        return report or CheckReport(results=(_result(),), repo_root=pack.root, trusted=True)

    monkeypatch.setattr(sk.check, "load_checks_pack", fake_load)
    monkeypatch.setattr(sk.check, "run_checks", fake_run)


def test_check_config_ok(tmp_path, monkeypatch):
    import sk.cli.commands.check as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, 'schema = 1\n[checks]\nlint = "ruff check ."\n')
    result = CliRunner().invoke(app, ["check", "--check-config", "--trust-project-checks"])
    assert result.exit_code == 0, result.output
    assert "Checks config OK" in result.output


def test_check_config_error_exits_2(tmp_path, monkeypatch):
    import sk.cli.commands.check as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, 'schema = 1\n[checks]\nlint = ""\n')
    result = CliRunner().invoke(app, ["check", "--check-config"])
    assert result.exit_code == 2
    assert "error:" in result.output


def test_check_config_untrusted_is_a_warning_only(tmp_path, monkeypatch):
    import sk.cli.commands.check as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, 'schema = 1\n[checks]\nlint = "ruff check ."\n')
    result = CliRunner().invoke(app, ["check", "--check-config"])
    assert result.exit_code == 0, result.output
    assert "warnings only" in result.output


def test_explain_prints_loaded_checks(tmp_path, monkeypatch):
    import sk.cli.commands.check as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, 'schema = 1\n[checks]\nlint = "ruff check ."\n')
    result = CliRunner().invoke(app, ["check", "--explain", "--trust-project-checks"])
    assert result.exit_code == 0, result.output
    assert "ruff check ." in result.output
    assert "trusted: yes" in result.output


def test_untrusted_project_exits_2_and_runs_nothing(tmp_path, monkeypatch):
    import sk.agent
    import sk.cli.commands.check as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, 'schema = 1\n[checks]\nlint = "ruff check ."\n')
    called = {"n": 0}

    def boom(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("untrusted checks must not run")

    monkeypatch.setattr(sk.agent, "_gated_dispatch", boom)
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == 2
    assert "untrusted" in result.output
    assert called["n"] == 0


def test_json_pass_exits_0(monkeypatch):
    import sk.cli.commands.check as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    _patch_runner(monkeypatch)
    result = CliRunner().invoke(app, ["check", "--json", "--trust-project-checks"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["status"] == "pass"
    assert data["results"][0]["id"] == "lint"


def test_json_fail_exits_1(monkeypatch):
    import sk.cli.commands.check as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    _patch_runner(
        monkeypatch,
        CheckReport(results=(_result("fail"),), repo_root="/tmp/repo", trusted=True),
    )
    result = CliRunner().invoke(app, ["check", "--json", "--trust-project-checks"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["status"] == "fail"
    assert data["results"][0]["diagnostics"][0]["code"] == "F401"


def test_unknown_exits_1(monkeypatch):
    import sk.cli.commands.check as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    _patch_runner(
        monkeypatch,
        CheckReport(
            results=(_result("unknown", note="timed out"),),
            repo_root="/tmp/repo",
            trusted=True,
        ),
    )
    result = CliRunner().invoke(app, ["check", "--trust-project-checks"])
    assert result.exit_code == 1
    assert "unknown" in result.output.lower()


def test_compact_report_shows_diagnostics(monkeypatch):
    import sk.cli.commands.check as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    _patch_runner(
        monkeypatch,
        CheckReport(results=(_result("fail"),), repo_root="/tmp/repo", trusted=True),
    )
    result = CliRunner().invoke(app, ["check", "--trust-project-checks"])
    assert result.exit_code == 1
    assert "src/a.py:1:2" in result.output
    assert "F401" in result.output


def test_trust_flag_and_changed_are_passed_through(monkeypatch):
    import sk.cli.commands.check as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    seen: dict = {}
    _patch_runner(monkeypatch, seen=seen)
    result = CliRunner().invoke(
        app, ["check", "--trust-project-checks", "--changed", "--session", "ci"]
    )
    assert result.exit_code == 0, result.output
    assert seen["load"]["trust_project"] is True
    assert seen["run"]["changed"] is True
    assert seen["run"]["session"] == "ci"


def test_yes_and_allow_flow_into_the_approver(monkeypatch):
    import sk.cli.commands.check as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    seen: dict = {}
    _patch_runner(monkeypatch, seen=seen)
    result = CliRunner().invoke(
        app,
        [
            "check",
            "--trust-project-checks",
            "--yes",
            "--allow",
            "shell:ruff",
            "--deny",
            "shell:rm",
        ],
    )
    assert result.exit_code == 0, result.output
    approve = seen["run"]["approve"]
    assert approve("shell", {"cmd": "ruff check ."}) is True
    assert approve("shell", {"cmd": "rm -rf /"}) is False


def test_invalid_max_diagnostics_exits_2(monkeypatch):
    import sk.cli.commands.check as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    result = CliRunner().invoke(app, ["check", "--max-diagnostics", "0"])
    assert result.exit_code == 2
    assert "at least 1" in result.output
