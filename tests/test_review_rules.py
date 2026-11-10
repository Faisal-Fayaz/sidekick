"""Built-in rule catalog plus deterministic positive/negative fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sk.cli import app
from sk.review.checks import deterministic_findings
from sk.review.diff import DiffModel, FileDiff, Hunk
from sk.review.pack import BUILTIN_RULES, load_review_pack
from sk.review.schema import CATEGORIES, SEVERITIES

RULES_ROOT = Path(__file__).parent / "review" / "rules"
EXPECTED_FILES = sorted(RULES_ROOT.glob("**/expected.json"))


def _diff_for(fixture: Path) -> DiffModel:
    lines = fixture.read_text().splitlines()
    hunk = Hunk(
        old_start=0,
        old_count=0,
        new_start=1,
        new_count=len(lines),
        header=f"@@ -0,0 +1,{len(lines)} @@",
        lines=tuple(f"+{line}" for line in lines),
    )
    return DiffModel(
        target="worktree",
        repo_root=str(fixture.parent),
        ref="HEAD",
        files=(
            FileDiff(
                path=fixture.name,
                status="A",
                additions=len(lines),
                hunks=(hunk,),
            ),
        ),
        changed_lines=len(lines),
    )


def test_catalog_has_at_least_ten_complete_rules():
    assert len(BUILTIN_RULES) >= 10
    ids = [rule.id for rule in BUILTIN_RULES]
    assert len(ids) == len(set(ids))
    for rule in BUILTIN_RULES:
        assert rule.severity in SEVERITIES
        assert rule.category in CATEGORIES
        assert rule.applies_to
        assert rule.guidance.strip()
        assert rule.protected is rule.id.startswith("security/")
        fixture = RULES_ROOT / rule.id
        for name in ("positive.py", "negative.py", "expected.json"):
            assert (fixture / name).is_file(), f"{rule.id} is missing {name}"


@pytest.mark.parametrize(
    "expected_path",
    EXPECTED_FILES,
    ids=[p.parent.relative_to(RULES_ROOT).as_posix() for p in EXPECTED_FILES],
)
def test_fixture_matches_rule_metadata(expected_path: Path):
    expected = json.loads(expected_path.read_text())
    rule_id = expected_path.parent.relative_to(RULES_ROOT).as_posix()
    assert expected["rule_id"] == rule_id
    rule = next(rule for rule in BUILTIN_RULES if rule.id == rule_id)
    assert expected["severity"] == rule.severity
    assert expected["category"] == rule.category

    positive = deterministic_findings(_diff_for(expected_path.parent / "positive.py"))
    assert positive, f"{rule_id} positive fixture produced no finding"
    assert {finding.rule_id for finding in positive} == {rule_id}
    for finding in positive:
        assert finding.severity == expected["severity"]
        assert finding.category == expected["category"]
        assert finding.source == "check"
    assert sorted(finding.line for finding in positive) == sorted(expected["positive_lines"])

    negative = deterministic_findings(_diff_for(expected_path.parent / "negative.py"))
    assert negative == []
    assert expected["negative_lines"] == []


def test_check_config_reports_no_fixture_warnings():
    result = CliRunner().invoke(app, ["review", "--check-config"])
    assert result.exit_code == 0, result.output
    assert "Review pack OK" in result.output
    assert "warnings only" not in result.output
    assert "fixture" not in result.output


def test_missing_fixture_is_a_warning_not_an_error(tmp_path: Path):
    (tmp_path / "tests" / "review" / "rules").mkdir(parents=True)
    pack = load_review_pack(
        cwd=str(tmp_path),
        user_path=tmp_path / "missing-user.toml",
        project_path=tmp_path / "missing-project.toml",
    )
    assert pack.errors == ()
    warnings = [message for level, message in pack.check() if level == "warning"]
    assert len(warnings) == len(BUILTIN_RULES)
    assert all("fixture" in message for message in warnings)


def test_correctness_rules_are_overridable_but_security_rules_are_not(tmp_path: Path):
    pack = load_review_pack(
        cwd=str(tmp_path),
        user_path=tmp_path / "missing-user.toml",
        project_path=tmp_path / "missing-project.toml",
        overrides={"disable_rules": ["correctness/bare-except"]},
    )
    assert pack.errors == ()
    assert all(rule.id != "correctness/bare-except" for rule in pack.rules)

    protected = load_review_pack(
        cwd=str(tmp_path),
        user_path=tmp_path / "missing-user.toml",
        project_path=tmp_path / "missing-project.toml",
        overrides={"disable_rules": ["security/weak-hash"]},
    )
    assert any("protected" in error for error in protected.errors)
