"""Local review feedback: two rejects suppress, two accepts pin (#514)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from typer.testing import CliRunner

import sk.review_feedback as fb
from sk.cli import app
from sk.review.schema import Finding


def finding(rule: str = "security/unsafe-yaml-load", file: str = "src/a.py") -> Finding:
    return Finding(
        id="F001",
        severity="major",
        category="security",
        file=file,
        line=1,
        end_line=None,
        evidence="yaml.load(x)",
        problem="unsafe loader",
        rule_id=rule,
        confidence="medium",
    )


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from sk import config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.toml")
    yield tmp_path


def test_invalid_records_are_refused_without_writing(isolated):
    assert "invalid rule id" in fb.record("../escape", "reject")
    assert "must be accept or reject" in fb.record("rule/x", "maybe")
    assert not (isolated / fb.FEEDBACK_FILE).exists()


def test_two_rejects_suppress_a_rule_but_one_does_not(isolated):
    fb.record("rule/x", "reject")
    kept, suppressed = fb.apply([finding("rule/x")])
    assert len(kept) == 1 and suppressed == 0

    fb.record("rule/x", "reject")
    kept, suppressed = fb.apply([finding("rule/x"), finding("other/y")])
    assert [f.rule_id for f in kept] == ["other/y"] and suppressed == 1


def test_file_scoped_rejects_only_affect_that_path(isolated):
    fb.record("rule/x", "reject", "src/a.py")
    fb.record("rule/x", "reject", "src/a.py")
    kept, suppressed = fb.apply([finding("rule/x", "src/a.py"), finding("rule/x", "src/b.py")])
    assert [f.file for f in kept] == ["src/b.py"] and suppressed == 1


def test_two_accepts_pin_a_rule_and_win_over_suppression(isolated):
    fb.record("rule/x", "accept")
    fb.record("rule/x", "accept")
    fb.record("rule/x", "reject")
    fb.record("rule/x", "reject")
    kept, suppressed = fb.apply([finding("rule/x")])
    assert len(kept) == 1 and suppressed == 0
    assert kept[0].confidence == "high"


def test_a_project_file_cannot_add_or_remove_feedback(isolated, monkeypatch):
    (isolated / ".sidekick.toml").write_text('feedback = ["reject rule/x"]\n')
    assert fb.learned() == fb.Learned(frozenset(), frozenset(), frozenset())
    monkeypatch.chdir(isolated)
    assert fb.learned().trusted == frozenset()


def test_forget_removes_only_the_named_rule(isolated):
    fb.record("a/x", "reject")
    fb.record("b/y", "reject")
    assert "Forgot" in fb.forget("a/x")
    assert sorted(e.rule_id for e in fb.entries()) == ["b/y"]


def test_summary_is_inspectable(isolated):
    fb.record("a/x", "reject")
    fb.record("a/x", "reject")
    fb.record("b/y", "accept")
    fb.record("b/y", "accept")
    lines = fb.summary()
    assert any("a/x" in line and "suppressed" in line for line in lines)
    assert any("b/y" in line and "trusted" in line for line in lines)


def test_cli_record_list_and_forget(isolated):
    runner = CliRunner()
    result = runner.invoke(app, ["review", "feedback", "--rule", "rule/x", "--reject"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["review", "feedback", "--rule", "rule/x", "--reject"])
    assert result.exit_code == 0
    listed = runner.invoke(app, ["review", "feedback", "--list"])
    assert "suppressed" in listed.output
    forgotten = runner.invoke(app, ["review", "feedback", "--rule", "rule/x", "--forget"])
    assert forgotten.exit_code == 0
    assert fb.entries() == []


def test_review_command_still_works_as_a_group(isolated):
    result = CliRunner().invoke(app, ["review", "--help"])
    assert result.exit_code == 0
    assert "feedback" in result.output


def test_pinned_rule_confidence_only_changes_when_needed(isolated):
    fb.record("rule/x", "accept")
    fb.record("rule/x", "accept")
    already = replace(finding(), confidence="high")
    kept, _ = fb.apply([already])
    assert kept[0] is already


def test_suppression_flows_through_the_review_engine(tmp_path, monkeypatch):
    """The integration: two rejects drop a finding and the report says so."""
    from sk import config as config_mod
    from sk.review.engine import ReviewOptions, review

    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "app.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "add"],
        cwd=repo,
        check=True,
    )
    (repo / "app.py").write_text('y = eval("1")\n')

    # Two rejects for the deterministic rule the diff above triggers.
    import sk.review_feedback as fb_local

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    fb_local.record("security/dynamic-eval", "reject")
    fb_local.record("security/dynamic-eval", "reject")

    class NoModel:
        def __call__(self, *a, **k):
            return '{"findings": []}'

    report = review(
        cwd=str(repo),
        cfg=__import__("sk.config", fromlist=["Config"]).Config(
            model="t", base_url="http://x/v1", api_key="x"
        ),
        completer=NoModel(),
        options=ReviewOptions(assess="never"),
    )
    assert all(f.rule_id != "security/dynamic-eval" for f in report.findings)
    assert report.dropped >= 1
    assert any("suppressed by local review feedback" in w for w in report.pack_warnings)
