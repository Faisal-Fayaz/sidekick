"""CLI/slash wiring for the review engine."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from sk.cli import app
from sk.config import Config
from sk.review.schema import Finding, ReviewReport


def _cfg():
    return Config(provider="ollama", model="fake", base_url="http://x/v1", api_key="x")


def _report(severity="major"):
    f = Finding(
        id="F001",
        severity=severity,
        category="correctness",
        file="app.py",
        line=1,
        end_line=None,
        evidence="x = 1",
        problem="problem",
        impact="impact",
        suggestion="fix",
        confidence="high",
        rule_id="test/rule",
    )
    return ReviewReport(
        target="worktree",
        ref="HEAD",
        repo_root="/tmp",
        findings=[f],
        files_reviewed=1,
        changed_lines=1,
        reviewed_lines=1,
    )


def test_review_cli_json(monkeypatch):
    import sk.cli.commands.review as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.setattr(mod, "review", lambda **kwargs: _report())
    result = CliRunner().invoke(app, ["review", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["findings"][0]["file"] == "app.py"


def test_review_cli_fail_on_exit_code(monkeypatch):
    import sk.cli.commands.review as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.setattr(mod, "review", lambda **kwargs: _report("major"))
    result = CliRunner().invoke(app, ["review", "--fail-on", "major"])
    assert result.exit_code == 1
    assert "MAJOR" in result.output


def test_review_cli_requires_one_target(monkeypatch):
    import sk.cli.commands.review as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    result = CliRunner().invoke(app, ["review", "--base", "main", "--staged"])
    assert result.exit_code == 1
    assert "choose one target" in result.output


def test_review_cli_post_requires_pr(monkeypatch):
    import sk.cli.commands.review as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    result = CliRunner().invoke(app, ["review", "--post"])
    assert result.exit_code == 1
    assert "--post only applies" in result.output


def test_review_cli_forced_post_failure_exits_nonzero(monkeypatch):
    import sk.cli.commands.review as mod

    report = _report()
    report.post_note = "posting failed: no credentials"
    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.setattr(mod, "review", lambda **kwargs: report)
    result = CliRunner().invoke(app, ["review", "--pr", "7", "--post", "--json"])
    assert result.exit_code == 1


def test_review_cli_passes_assess_and_post_flags(monkeypatch):
    import sk.cli.commands.review as mod

    seen = {}

    def fake_review(**kwargs):
        seen.update(kwargs)
        return _report()

    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.setattr(mod, "review", fake_review)
    result = CliRunner().invoke(
        app,
        ["review", "--pr", "7", "--assess", "--no-post", "--intent", "fix login", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert seen["pr"] == 7
    assert seen["options"].assess == "always"
    assert seen["options"].post == "never"
    assert seen["options"].intent == "fix login"


def test_slash_review_uses_engine(monkeypatch):
    import sk.review.engine as eng
    from sk.slash import handle

    seen = {}

    def fake_review(**kwargs):
        seen.update(kwargs)
        return _report()

    monkeypatch.setattr(eng, "review", fake_review)
    out = handle("/review --staged", session="s", cfg=_cfg(), state={})
    assert out.handled is True
    assert "MAJOR" in out.text
    assert seen["staged"] is True
    assert seen["options"].post == "auto"


def test_slash_review_legacy_base(monkeypatch):
    import sk.review.engine as eng
    from sk.slash import handle

    seen = {}

    def fake_review(**kwargs):
        seen.update(kwargs)
        return _report()

    monkeypatch.setattr(eng, "review", fake_review)
    out = handle("/review main", session="s", cfg=_cfg(), state={})
    assert out.handled is True
    assert seen["base"] == "main"


def test_slash_review_rejects_post_without_pr():
    from sk.slash import handle

    out = handle("/review --post", session="s", cfg=_cfg(), state={})
    assert out.handled is True
    assert "only applies" in out.text


def test_review_cli_sarif(monkeypatch):
    import sk.cli.commands.review as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.setattr(mod, "review", lambda **kwargs: _report())
    result = CliRunner().invoke(app, ["review", "--sarif"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["version"] == "2.1.0"


def test_review_cli_rejects_conflicting_flags(monkeypatch):
    import sk.cli.commands.review as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    for args in (
        ["review", "--assess", "--no-assess"],
        ["review", "--pr", "1", "--post", "--no-post"],
        ["review", "--json", "--sarif"],
        ["review", "--fail-on", "nope"],
    ):
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 1, args


def test_review_cli_reports_review_error(monkeypatch):
    import sk.cli.commands.review as mod
    from sk.review.engine import ReviewError

    def boom(**kwargs):
        raise ReviewError("not a git repo")

    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.setattr(mod, "review", boom)
    result = CliRunner().invoke(app, ["review"])
    assert result.exit_code == 1
    assert "not a git repo" in result.output


def test_review_cli_markdown_post_note(monkeypatch):
    import sk.cli.commands.review as mod

    report = _report()
    report.post_note = "credentials unavailable"
    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.setattr(mod, "review", lambda **kwargs: report)
    result = CliRunner().invoke(app, ["review", "--pr", "1"])
    assert result.exit_code == 0, result.output
    assert "credentials unavailable" in result.output


def test_review_cli_verify_is_opt_in_and_passes_an_approver(monkeypatch):
    import sk.cli.commands.review as mod

    seen = {}

    def fake_review(**kwargs):
        seen.update(kwargs)
        return _report()

    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.setattr(mod, "review", fake_review)

    result = CliRunner().invoke(app, ["review", "--verify", "--yes", "--json"])
    assert result.exit_code == 0, result.output
    assert seen["options"].verify is True
    assert callable(seen["approve"])

    result = CliRunner().invoke(app, ["review", "--json"])
    assert result.exit_code == 0, result.output
    assert seen["options"].verify is False
    assert seen["approve"] is None


def test_review_cli_verification_fail_or_unknown_exits_1(monkeypatch):
    import sk.cli.commands.review as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    for status in ("fail", "unknown"):
        report = _report()
        report.verification = {
            "status": status,
            "counts": {status: 1},
            "results": [],
            "errors": [],
            "warnings": [],
        }
        monkeypatch.setattr(mod, "review", lambda **kwargs: report)
        result = CliRunner().invoke(app, ["review", "--verify", "--json"])
        assert result.exit_code == 1, (status, result.output)


def test_review_cli_verification_pass_does_not_change_exit(monkeypatch):
    import sk.cli.commands.review as mod

    report = _report()
    report.verification = {
        "status": "pass",
        "counts": {"pass": 1},
        "results": [],
        "errors": [],
        "warnings": [],
    }
    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.setattr(mod, "review", lambda **kwargs: report)
    result = CliRunner().invoke(app, ["review", "--verify", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["verification"]["status"] == "pass"


def test_review_cli_renders_verification_markdown(monkeypatch):
    import sk.cli.commands.review as mod

    report = _report()
    report.verification = {
        "status": "fail",
        "counts": {"fail": 1},
        "results": [
            {
                "id": "lint",
                "status": "fail",
                "command": "ruff check app.py",
                "note": "",
                "raw": "",
                "diagnostics": [
                    {
                        "file": "app.py",
                        "line": 1,
                        "column": 1,
                        "code": "F401",
                        "message": "unused import",
                    }
                ],
            }
        ],
        "errors": [],
        "warnings": [],
    }
    monkeypatch.setattr(mod, "_cfg", _cfg)
    monkeypatch.setattr(mod, "review", lambda **kwargs: report)
    result = CliRunner().invoke(app, ["review", "--verify"])
    assert result.exit_code == 1
    assert "Verification" in result.output
    assert "F401" in result.output
