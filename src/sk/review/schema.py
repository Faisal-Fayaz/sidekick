"""Finding/assessment schema, evidence validation, and report serialization.

The hard rule: no finding without a real file, a line in the diff, and evidence
that actually appears in the file (or the diff for deleted code). Invalid
findings are dropped and counted, never shown silently.

The worth-it assessment is a separate, always-advisory channel. It cannot
change an exit code or a SARIF result.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SEVERITIES = ("blocker", "major", "minor", "nit")
CATEGORIES = ("correctness", "security", "performance", "maintainability", "tests", "style")
CONFIDENCES = ("high", "medium", "low")
VERDICTS = ("proceed", "proceed-with-changes", "reconsider")
INTENT_SOURCES = ("pr-body", "commit", "branch", "user", "inferred")
SEVERITY_RANK = {"blocker": 0, "major": 1, "minor": 2, "nit": 3}
_SARIF_LEVEL = {"blocker": "error", "major": "error", "minor": "warning", "nit": "note"}


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str
    category: str
    file: str
    line: int
    end_line: int | None
    evidence: str
    problem: str
    impact: str = ""
    suggestion: str = ""
    confidence: str = "medium"
    rule_id: str = ""
    source: str = "model"

    def sort_key(self) -> tuple[int, str, int, str]:
        return (SEVERITY_RANK.get(self.severity, 9), self.file, self.line, self.id)


@dataclass(frozen=True)
class AssessmentQuestion:
    question: str
    evidence: str
    alternative: str = ""


@dataclass(frozen=True)
class Assessment:
    stated_intent: str
    intent_source: str
    signals: tuple[str, ...] = ()
    questions: tuple[AssessmentQuestion, ...] = ()
    risks: tuple[str, ...] = ()
    verdict: str = "proceed"
    confidence: str = "low"
    assumptions: tuple[str, ...] = ()
    advisory: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "stated_intent": self.stated_intent,
            "intent_source": self.intent_source,
            "signals": list(self.signals),
            "questions": [
                {
                    "question": q.question,
                    "evidence": q.evidence,
                    "alternative": q.alternative,
                }
                for q in self.questions
            ],
            "risks": list(self.risks),
            "verdict": self.verdict,
            "confidence": self.confidence,
            "assumptions": list(self.assumptions),
            "advisory": True,
        }


@dataclass(frozen=True)
class Skip:
    path: str
    reason: str


def _verification_lines(verification: dict[str, Any]) -> list[str]:
    """Render the check verdict without pretending it is a review finding."""
    status = str(verification.get("status") or "unknown")
    counts = verification.get("counts") or {}
    summary = (
        ", ".join(f"{count} {name}" for name, count in counts.items() if count) or "no checks ran"
    )
    lines = ["", "### Verification", "", f"**Status: {status.upper()}** ({summary})"]
    for result in list(verification.get("results") or [])[:20]:
        check_id = str(result.get("id") or "check")
        check_status = str(result.get("status") or "unknown")
        command = str(result.get("command") or "")
        if len(command) > 160:
            command = command[:157] + "..."
        note = str(result.get("note") or "")
        suffix = f" — {note}" if note else ""
        lines.append("")
        lines.append(f"- `{check_id}` **{check_status.upper()}** `{command}`{suffix}")
        diagnostics = list(result.get("diagnostics") or [])
        for diagnostic in diagnostics[:10]:
            location = str(diagnostic.get("file") or "(no file)")
            line_no = diagnostic.get("line")
            if line_no:
                location += f":{line_no}"
                column = diagnostic.get("column")
                if column:
                    location += f":{column}"
            code = str(diagnostic.get("code") or "")
            message = str(diagnostic.get("message") or "")
            lines.append(f"  - `{location}` {code} {message}".rstrip())
        if len(diagnostics) > 10:
            lines.append(f"  - ... {len(diagnostics) - 10} more diagnostics")
        if check_status in ("fail", "unknown") and not diagnostics:
            raw = str(result.get("raw") or "").strip()
            if raw:
                lines.append("  ```")
                lines.extend(f"  {raw_line}" for raw_line in raw.splitlines()[:6])
                lines.append("  ```")
    for error in list(verification.get("errors") or [])[:5]:
        lines.append(f"- **CONFIG ERROR:** {error}")
    for warning in list(verification.get("warnings") or [])[:5]:
        lines.append(f"- warning: {warning}")
    return lines


@dataclass
class ReviewReport:
    target: str
    ref: str
    repo_root: str
    findings: list[Finding] = field(default_factory=list)
    assessment: Assessment | None = None
    files_reviewed: int = 0
    files_skipped: int = 0
    changed_lines: int = 0
    reviewed_lines: int = 0
    dropped: int = 0
    model: str = ""
    provider: str = ""
    review_pack_hash: str = ""
    fail_on: str = "never"
    pack_warnings: list[str] = field(default_factory=list)
    latency_ms: int = 0
    token_cost: float = 0.0
    truncated: bool = False
    errors: list[str] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)
    posted_url: str = ""
    post_note: str = ""
    verification: dict[str, Any] | None = None

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in SEVERITIES}
        for f in self.findings:
            if f.severity in out:
                out[f.severity] += 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "ref": self.ref,
            "repo_root": self.repo_root,
            "findings": [_finding_dict(f) for f in self.findings],
            "assessment": self.assessment.to_dict() if self.assessment else None,
            "summary": {
                "counts": self.counts(),
                "files_reviewed": self.files_reviewed,
                "files_skipped": self.files_skipped,
                "changed_lines": self.changed_lines,
                "reviewed_lines": self.reviewed_lines,
                "dropped": self.dropped,
                "truncated": self.truncated,
            },
            "model": self.model,
            "provider": self.provider,
            "review_pack_hash": self.review_pack_hash,
            "fail_on": self.fail_on,
            "pack_warnings": list(self.pack_warnings),
            "latency_ms": self.latency_ms,
            "token_cost": round(self.token_cost, 6),
            "errors": list(self.errors),
            "skipped": [{"path": s.path, "reason": s.reason} for s in self.skipped],
            "posted_url": self.posted_url,
            "post_note": self.post_note,
            "verification": self.verification,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)

    def to_markdown(self) -> str:
        counts = self.counts()
        lines = [
            f"## Review — {self.ref}",
            "",
            f"`{self.repo_root}` · model `{self.model or 'n/a'}` · "
            f"{self.files_reviewed} file(s) reviewed · "
            f"{counts['blocker']} blocker / {counts['major']} major / "
            f"{counts['minor']} minor / {counts['nit']} nit",
        ]
        if self.review_pack_hash:
            lines.append(f"Review pack: `{self.review_pack_hash}`")
        if self.pack_warnings:
            lines.append("Review pack warnings: " + "; ".join(self.pack_warnings[:3]))
        if self.changed_lines:
            pct = round(100.0 * self.reviewed_lines / self.changed_lines, 1)
            lines.append(
                f"Changed-line coverage: {pct}% ({self.reviewed_lines}/{self.changed_lines})"
            )
        if self.truncated:
            lines.append("_Diff truncated by the review budget; coverage is partial._")
        if self.skipped:
            lines.append(
                "Skipped: " + ", ".join(f"`{s.path}` ({s.reason})" for s in self.skipped[:8])
            )
        if self.dropped:
            lines.append(f"_Dropped {self.dropped} invalid finding(s) without evidence._")
        if self.assessment:
            lines.extend(["", "### Change assessment (advisory)", ""])
            a = self.assessment
            lines.append(f"**Verdict: {a.verdict}** ({a.confidence} confidence; advisory)")
            lines.append("")
            lines.append(f"Stated intent: {a.stated_intent}")
            lines.append(f"Intent source: `{a.intent_source}`")
            if a.signals:
                lines.append("")
                lines.append("Signals: " + "; ".join(a.signals))
            for q in a.questions:
                lines.append("")
                lines.append(f"- **{q.question}**")
                lines.append(f"  Evidence: {q.evidence}")
                if q.alternative:
                    lines.append(f"  Alternative: {q.alternative}")
            if a.risks:
                lines.append("")
                lines.append("Risks: " + "; ".join(a.risks))
            if a.assumptions:
                lines.append("")
                lines.append("Assumptions: " + "; ".join(a.assumptions))
        if self.verification:
            lines.extend(_verification_lines(self.verification))
        lines.append("")
        if not self.findings:
            lines.append("_No evidence-backed findings._")
        for f in sorted(self.findings, key=lambda x: x.sort_key()):
            rng = f"{f.line}" + (f"-{f.end_line}" if f.end_line and f.end_line != f.line else "")
            lines.append(f"### {f.severity.upper()} · {f.category} · `{f.file}:{rng}`")
            lines.append("")
            if f.evidence:
                lines.append(f"> `{_safe_evidence(f.evidence)}`")
                lines.append("")
            lines.append(f"**Problem:** {f.problem}")
            if f.impact:
                lines.append(f"**Impact:** {f.impact}")
            if f.suggestion:
                lines.append(f"**Fix:** {f.suggestion}")
            if f.rule_id:
                lines.append(f"`{f.rule_id}` · source `{f.source}` · confidence `{f.confidence}`")
            lines.append("")
        if self.errors:
            lines.append("")
            lines.append("Review warnings: " + "; ".join(self.errors[:5]))
        if self.posted_url:
            lines.append("")
            lines.append(f"Posted review: {self.posted_url}")
        elif self.post_note:
            lines.append("")
            lines.append(f"Posting: {self.post_note}")
        return "\n".join(lines).rstrip() + "\n"

    def to_sarif(self) -> dict[str, Any]:
        rules = sorted({f.rule_id for f in self.findings if f.rule_id})
        results = []
        for f in sorted(self.findings, key=lambda x: x.sort_key()):
            results.append(
                {
                    "ruleId": f.rule_id or f.category,
                    "level": _SARIF_LEVEL.get(f.severity, "warning"),
                    "message": {
                        "text": f.problem + (f"\n\nFix: {f.suggestion}" if f.suggestion else "")
                    },
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": f.file},
                                "region": {
                                    "startLine": f.line,
                                    **(
                                        {"endLine": f.end_line}
                                        if f.end_line and f.end_line != f.line
                                        else {}
                                    ),
                                },
                            }
                        }
                    ],
                }
            )
        return {
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "sidekick-review",
                            "informationUri": "https://github.com/Faisal-Fayaz/sidekick",
                            "rules": [{"id": r} for r in rules],
                        }
                    },
                    "results": results,
                }
            ],
        }


def _finding_dict(f: Finding) -> dict[str, Any]:
    return {
        "id": f.id,
        "severity": f.severity,
        "category": f.category,
        "file": f.file,
        "line": f.line,
        "end_line": f.end_line,
        "evidence": f.evidence,
        "problem": f.problem,
        "impact": f.impact,
        "suggestion": f.suggestion,
        "confidence": f.confidence,
        "rule_id": f.rule_id,
        "source": f.source,
    }


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _safe_evidence(text: str) -> str:
    """One-line, inline-code-safe evidence for Markdown/PR comments."""
    clean = _norm(text).replace("`", "'")
    return clean[:300]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except Exception:
        return ""


def finding_from_dict(obj: dict[str, Any], index: int = 0) -> Finding | None:
    if not isinstance(obj, dict):
        return None
    file = str(obj.get("file") or obj.get("path") or "").strip().lstrip("./")
    try:
        line = int(obj.get("line") or 0)
    except Exception:
        line = 0
    problem = _norm(str(obj.get("problem") or obj.get("message") or ""))
    evidence = _norm(str(obj.get("evidence") or ""))
    if not file or line <= 0 or not problem or not evidence:
        return None
    sev = str(obj.get("severity") or "minor").strip().lower()
    if sev not in SEVERITIES:
        sev = "minor"
    cat = str(obj.get("category") or "correctness").strip().lower()
    if cat not in CATEGORIES:
        cat = "correctness"
    conf = str(obj.get("confidence") or "medium").strip().lower()
    if conf not in CONFIDENCES:
        conf = "medium"
    end_line = obj.get("end_line")
    if end_line is not None:
        try:
            end_line = int(end_line)
        except Exception:
            end_line = None
    return Finding(
        id=str(obj.get("id") or f"finding-{index + 1:03d}"),
        severity=sev,
        category=cat,
        file=file,
        line=line,
        end_line=end_line or None,
        evidence=evidence,
        problem=problem,
        impact=_norm(str(obj.get("impact") or "")),
        suggestion=_norm(str(obj.get("suggestion") or obj.get("fix") or "")),
        confidence=conf,
        rule_id=str(obj.get("rule_id") or "").strip(),
        source=str(obj.get("source") or "model").strip(),
    )


def validate_findings(
    findings: Iterable[Finding],
    diff: Any,
    *,
    repo_root: str,
    context_lines: int = 3,
) -> tuple[list[Finding], list[tuple[Finding, str]]]:
    """Keep only findings with a real path, diff line, and matching evidence."""
    valid: list[Finding] = []
    dropped: list[tuple[Finding, str]] = []
    by_path = {f.path: f for f in getattr(diff, "files", ())}
    changed = {p: fd.changed_new_lines() for p, fd in by_path.items()}
    in_hunk = {p: fd.new_lines_in_hunk() for p, fd in by_path.items()}
    seen: set[tuple[str, int, str]] = set()
    for f in findings:
        path = f.file.replace("\\", "/").lstrip("./")
        fd = by_path.get(path)
        if fd is None:
            dropped.append((f, "file is not part of the reviewed diff"))
            continue
        if fd.binary or fd.skipped:
            dropped.append((f, "file was skipped"))
            continue
        # Allow the changed line and nearby context, but nothing outside the
        # hunk: a hallucinated line number is the most common failure.
        allowed = set(changed.get(path, set()))
        for ln in range(max(1, f.line - context_lines), f.line + context_lines + 1):
            if ln in in_hunk.get(path, set()):
                allowed.add(ln)
        if f.line not in allowed and f.line not in in_hunk.get(path, set()):
            dropped.append((f, f"line {f.line} is not in the diff"))
            continue
        file_path = Path(repo_root) / path
        body = _read_text(file_path)
        evidence = _norm(f.evidence)
        if body:
            if evidence not in _norm(body):
                # Deleted-line findings are still valid if the quote exists in
                # the diff itself.
                if evidence not in _norm(getattr(diff, "raw", "")):
                    dropped.append((f, "evidence does not appear in the file"))
                    continue
        elif evidence not in _norm(getattr(diff, "raw", "")):
            dropped.append((f, "evidence does not appear in the diff"))
            continue
        key = (path, f.line, f.rule_id or f.problem[:80])
        if key in seen:
            dropped.append((f, "duplicate finding"))
            continue
        seen.add(key)
        valid.append(f)
    valid.sort(key=lambda x: x.sort_key())
    return valid, dropped


def assessment_from_dict(obj: dict[str, Any]) -> Assessment | None:
    if not isinstance(obj, dict):
        return None
    verdict = str(obj.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        return None
    conf = str(obj.get("confidence") or "low").strip().lower()
    if conf not in CONFIDENCES:
        conf = "low"
    source = str(obj.get("intent_source") or "inferred").strip().lower()
    if source not in INTENT_SOURCES:
        source = "inferred"
    intent = _norm(str(obj.get("stated_intent") or ""))
    assumptions_raw = obj.get("assumptions") or []
    if isinstance(assumptions_raw, str):
        assumptions_raw = [assumptions_raw]
    assumptions = [str(x).strip() for x in assumptions_raw if str(x).strip()]
    if not intent:
        intent = "Not stated in the PR, commit message, or --intent."
        assumptions.append("Intent was not available; the assessment is limited to the diff.")
        source = "inferred"
    questions: list[AssessmentQuestion] = []
    for q in obj.get("questions") or []:
        if not isinstance(q, dict):
            continue
        question = _norm(str(q.get("question") or ""))
        evidence = _norm(str(q.get("evidence") or ""))
        if question and evidence:
            questions.append(
                AssessmentQuestion(
                    question=question,
                    evidence=evidence,
                    alternative=_norm(str(q.get("alternative") or "")),
                )
            )
    risks_raw = obj.get("risks") or []
    if isinstance(risks_raw, str):
        risks_raw = [risks_raw]
    signals_raw = obj.get("signals") or []
    if isinstance(signals_raw, str):
        signals_raw = [signals_raw]
    risks = [str(r).strip() for r in risks_raw if str(r).strip()]
    signals = [str(s).strip() for s in signals_raw if str(s).strip()]
    return Assessment(
        stated_intent=intent,
        intent_source=source,
        signals=tuple(signals),
        questions=tuple(questions),
        risks=tuple(risks),
        verdict=verdict,
        confidence=conf,
        assumptions=tuple(assumptions),
        advisory=True,
    )


def fail_on_threshold(findings: Iterable[Finding], fail_on: str) -> bool:
    """True when any finding meets or exceeds the fail-on severity."""
    level = (fail_on or "never").strip().lower()
    if level == "never":
        return False
    if level not in SEVERITY_RANK:
        level = "major"
    cutoff = SEVERITY_RANK.get(level, 1)
    return any(SEVERITY_RANK.get(f.severity, 9) <= cutoff for f in findings)
