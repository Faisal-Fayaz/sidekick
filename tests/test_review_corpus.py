"""Seeded, offline worth-it assessment corpus.

The corpus is data, not network calls: every case supplies its own scripted
model response. Running it twice must produce identical advisories.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sk.config import Config
from sk.review.diff import DiffModel, FileDiff, Hunk
from sk.review.engine import ReviewOptions, review

CASES_PATH = Path(__file__).parent / "review" / "assessment" / "cases.json"
CASES = json.loads(CASES_PATH.read_text())["cases"]


def _cfg() -> Config:
    return Config(provider="ollama", model="fake", base_url="http://x/v1", api_key="x")


def _completer(assessment: dict):
    payload = json.dumps(assessment)

    def call(cfg, *, system, user, max_tokens=4096):
        if "advisory change assessor" in system:
            return payload
        return '{"findings":[]}'

    return call


def _diff(case: dict, root: Path) -> DiffModel:
    files = []
    raw_parts = []
    changed = 0
    for spec in case["files"]:
        path = root / spec["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = spec["lines"]
        path.write_text("\n".join(lines) + "\n")
        hunk = Hunk(
            old_start=0,
            old_count=0,
            new_start=1,
            new_count=len(lines),
            header=f"@@ -0,0 +1,{len(lines)} @@",
            lines=tuple(f"+{line}" for line in lines),
        )
        files.append(
            FileDiff(
                path=spec["path"],
                status="A",
                additions=len(lines),
                hunks=(hunk,),
            )
        )
        raw_parts.append(
            f"diff --git a/{spec['path']} b/{spec['path']}\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            f"+++ b/{spec['path']}\n"
            f"{hunk.header}\n" + "\n".join(hunk.lines)
        )
        changed += len(lines)
    return DiffModel(
        target=case.get("target", "base:main"),
        repo_root=str(root),
        ref="main...HEAD",
        files=tuple(files),
        raw="\n".join(raw_parts),
        changed_lines=changed,
    )


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_assessment_corpus_is_grounded_and_deterministic(case: dict, tmp_path: Path):
    diff = _diff(case, tmp_path / case["name"])
    options = ReviewOptions(assess="always", intent=case["intent"])
    first = review(diff=diff, cfg=_cfg(), completer=_completer(case["assessment"]), options=options)
    second = review(
        diff=diff, cfg=_cfg(), completer=_completer(case["assessment"]), options=options
    )

    assert first.findings == []
    assert first.errors == []
    assert first.assessment is not None
    assert first.assessment == second.assessment
    assert first.findings == second.findings

    assessment = first.assessment
    expect = case["expect"]
    assert assessment.advisory is True
    assert assessment.verdict == expect["verdict"]
    assert assessment.intent_source == expect["intent_source"]
    assert assessment.stated_intent == case["intent"]
    for fragment in expect["signals_include"]:
        assert any(fragment in signal for signal in assessment.signals), fragment
    assert len(assessment.questions) == expect["questions"]
    assert [question.evidence for question in assessment.questions] == expect["question_evidence"]
    assert list(assessment.risks) == expect["risks"]
