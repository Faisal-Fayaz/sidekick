"""Finding/assessment validation and serialization — the honesty boundary."""

from __future__ import annotations

import json
from pathlib import Path

from sk.review.diff import DiffModel, FileDiff, Hunk
from sk.review.schema import (
    Assessment,
    Finding,
    ReviewReport,
    Skip,
    assessment_from_dict,
    fail_on_threshold,
    finding_from_dict,
    validate_findings,
)


def _diff(tmp_path: Path) -> DiffModel:
    (tmp_path / "app.py").write_text("x = 1\ny = eval(user)\n")
    return DiffModel(
        target="worktree",
        repo_root=str(tmp_path),
        ref="HEAD",
        raw="diff --git a/app.py b/app.py\n@@ -1,2 +1,2 @@\n x = 1\n+y = eval(user)\n",
        changed_lines=1,
        files=(
            FileDiff(
                path="app.py",
                additions=1,
                hunks=(
                    Hunk(
                        old_start=1,
                        old_count=1,
                        new_start=1,
                        new_count=2,
                        header="@@ -1,2 +1,2 @@",
                        lines=(" x = 1", "+y = eval(user)"),
                    ),
                ),
            ),
        ),
    )


def _finding(**kw):
    base = dict(
        id="",
        severity="major",
        category="security",
        file="app.py",
        line=2,
        end_line=None,
        evidence="y = eval(user)",
        problem="eval on untrusted input",
        impact="code execution",
        suggestion="parse instead",
        confidence="high",
        rule_id="test/eval",
    )
    base.update(kw)
    return Finding(**base)


def test_finding_from_dict_requires_file_line_problem_evidence():
    assert finding_from_dict({"file": "a.py", "line": 1, "problem": "x", "evidence": "y"})
    assert finding_from_dict({"file": "a.py", "line": 1, "problem": "", "evidence": "y"}) is None
    assert finding_from_dict({"file": "", "line": 1, "problem": "x", "evidence": "y"}) is None
    assert finding_from_dict({"file": "a.py", "line": 0, "problem": "x", "evidence": "y"}) is None


def test_finding_from_dict_normalizes_enums():
    f = finding_from_dict(
        {
            "file": "a.py",
            "line": 2,
            "problem": "p",
            "evidence": "e",
            "severity": "SEVERE",
            "category": "weird",
            "confidence": "certain",
        }
    )
    assert f is not None
    assert f.severity == "minor"
    assert f.category == "correctness"
    assert f.confidence == "medium"


def test_validate_keeps_evidence_backed_finding(tmp_path):
    valid, dropped = validate_findings([_finding()], _diff(tmp_path), repo_root=str(tmp_path))
    assert len(valid) == 1
    assert dropped == []


def test_validate_drops_hallucinated_line(tmp_path):
    valid, dropped = validate_findings(
        [_finding(line=99)], _diff(tmp_path), repo_root=str(tmp_path)
    )
    assert valid == []
    assert "not in the diff" in dropped[0][1]


def test_validate_drops_evidence_not_in_file_or_diff(tmp_path):
    valid, dropped = validate_findings(
        [_finding(evidence="y = totally_different")],
        _diff(tmp_path),
        repo_root=str(tmp_path),
    )
    assert valid == []
    assert "evidence" in dropped[0][1]


def test_validate_drops_unknown_file(tmp_path):
    valid, dropped = validate_findings(
        [_finding(file="missing.py")], _diff(tmp_path), repo_root=str(tmp_path)
    )
    assert valid == []
    assert "not part of" in dropped[0][1]


def test_validate_dedupes_same_rule_line(tmp_path):
    f = _finding()
    valid, dropped = validate_findings([f, f], _diff(tmp_path), repo_root=str(tmp_path))
    assert len(valid) == 1
    assert "duplicate" in dropped[0][1]


def test_assessment_requires_valid_verdict_and_evidence_questions():
    a = assessment_from_dict(
        {
            "stated_intent": "fix login",
            "intent_source": "pr-body",
            "verdict": "reconsider",
            "confidence": "high",
            "questions": [
                {"question": "Could a flag avoid this?", "evidence": "new migration"},
                {"question": "No evidence", "evidence": ""},
            ],
            "risks": ["schema rollback"],
            "assumptions": ["single tenant"],
        }
    )
    assert a is not None
    assert a.verdict == "reconsider"
    assert a.advisory is True
    assert len(a.questions) == 1
    assert assessment_from_dict({"verdict": "nope"}) is None


def test_assessment_missing_intent_is_recorded_as_inferred():
    a = assessment_from_dict({"verdict": "proceed", "confidence": "low"})
    assert a is not None
    assert a.intent_source == "inferred"
    assert "Not stated" in a.stated_intent
    assert any("Intent was not available" in x for x in a.assumptions)


def test_report_json_markdown_and_sarif_are_consistent(tmp_path):
    report = ReviewReport(
        target="worktree",
        ref="HEAD",
        repo_root=str(tmp_path),
        findings=[_finding()],
        assessment=Assessment(
            stated_intent="fix login",
            intent_source="user",
            signals=("one file",),
            questions=(),
            risks=("rollback",),
            verdict="proceed-with-changes",
            confidence="medium",
        ),
        files_reviewed=1,
        files_skipped=1,
        changed_lines=1,
        reviewed_lines=1,
        dropped=1,
        model="fake",
        provider="ollama",
        skipped=[Skip("uv.lock", "lockfile")],
        posted_url="https://example.test/pr/1",
    )
    data = json.loads(report.to_json())
    assert data["findings"][0]["file"] == "app.py"
    assert data["assessment"]["advisory"] is True
    assert data["summary"]["counts"]["major"] == 1
    md = report.to_markdown()
    assert "MAJOR" in md
    assert "Change assessment" in md
    assert "Posted review" in md
    sarif = report.to_sarif()
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"][0]["level"] == "error"


def test_fail_on_threshold():
    findings = [_finding(severity="major")]
    assert fail_on_threshold(findings, "never") is False
    assert fail_on_threshold(findings, "major") is True
    assert fail_on_threshold(findings, "blocker") is False
    assert fail_on_threshold(findings, "minor") is True
