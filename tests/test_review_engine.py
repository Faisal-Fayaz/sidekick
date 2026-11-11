"""Engine orchestration: deterministic checks, model findings, assessment, caps."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from sk.config import Config
from sk.review.diff import DiffModel, FileDiff, Hunk
from sk.review.engine import ASSESS_SYSTEM, ReviewOptions, review
from sk.review.intent import Intent, resolve_intent
from sk.review.signals import compute_signals


def _cfg(**kw):
    base = dict(provider="ollama", model="fake", base_url="http://example.test/v1", api_key="x")
    base.update(kw)
    return Config(**base)


def _git(cwd, *args):
    r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=30, cwd=str(cwd))
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("needs git")
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "test@example.com")
    _git(d, "config", "user.name", "Test")
    (d / "app.py").write_text("x = 1\n")
    _git(d, "add", ".")
    _git(d, "commit", "-qm", "initial")
    return d


def _completer(review_payload: str, assessment_payload: str | None = None, error=None):
    calls: list[str] = []

    def call(cfg, *, system, user, max_tokens=4096):
        calls.append(system)
        if error:
            raise RuntimeError(error)
        if "advisory change assessor" in system:
            return assessment_payload or (
                '{"stated_intent":"x","intent_source":"user","signals":[],'
                '"questions":[],"risks":[],"verdict":"proceed","confidence":"low",'
                '"assumptions":[]}'
            )
        return review_payload

    call.calls = calls  # type: ignore[attr-defined]
    return call


def _model_finding(line=2, evidence='y = eval("1")', problem="model issue", category="security"):
    import json

    return json.dumps(
        {
            "findings": [
                {
                    "file": "app.py",
                    "line": line,
                    "evidence": evidence,
                    "problem": problem,
                    "impact": "impact",
                    "suggestion": "fix it",
                    "severity": "major",
                    "category": category,
                    "confidence": "high",
                    "rule_id": "model/test",
                }
            ]
        }
    )


def test_review_collects_check_and_model_findings(repo):
    (repo / "app.py").write_text('x = 1\ny = eval("1")\n')
    report = review(
        cwd=str(repo),
        cfg=_cfg(),
        completer=_completer(_model_finding()),
        options=ReviewOptions(assess="never"),
    )
    assert report.files_reviewed == 1
    assert report.findings
    # The deterministic check wins the dedupe against the model restatement.
    assert any(f.source == "check" for f in report.findings)
    assert report.assessment is None
    assert "`app.py:2`" in report.to_markdown()


def test_review_drops_hallucinated_model_finding(repo):
    (repo / "app.py").write_text('x = 1\ny = eval("1")\n')
    report = review(
        cwd=str(repo),
        cfg=_cfg(),
        completer=_completer(_model_finding(line=99, evidence="not real")),
        options=ReviewOptions(assess="never"),
    )
    assert report.dropped >= 1
    assert not any(f.source == "model" for f in report.findings)


def test_review_model_error_is_reported_not_raised(repo):
    (repo / "app.py").write_text('x = 1\ny = eval("1")\n')
    report = review(
        cwd=str(repo),
        cfg=_cfg(),
        completer=_completer("", error="provider down"),
        options=ReviewOptions(assess="never"),
    )
    assert any("provider down" in e for e in report.errors)
    assert any(f.source == "check" for f in report.findings)
    assert report.reviewed_lines == 0


def test_review_assessment_is_advisory_and_uses_deterministic_signals(repo):
    (repo / "app.py").write_text("x = 2\n")
    payload = (
        '{"stated_intent":"invented intent","intent_source":"pr-body",'
        '"signals":["invented signal"],"questions":[{"question":"Is this needed?",'
        '"evidence":"x = 2","alternative":"keep x = 1"}],"risks":["risk"],'
        '"verdict":"reconsider","confidence":"high","assumptions":[]}'
    )
    report = review(
        cwd=str(repo),
        cfg=_cfg(),
        completer=_completer('{"findings":[]}', assessment_payload=payload),
        options=ReviewOptions(assess="always", intent="change x"),
    )
    assert report.assessment is not None
    assert report.assessment.advisory is True
    assert report.assessment.stated_intent == "change x"
    assert report.assessment.intent_source == "user"
    assert "invented signal" not in report.assessment.signals
    assert report.assessment.questions[0].alternative == "keep x = 1"


def test_review_assessment_auto_off_for_worktree_on_for_base(repo):
    (repo / "app.py").write_text("x = 2\n")
    work = review(
        cwd=str(repo),
        cfg=_cfg(),
        completer=_completer('{"findings":[]}'),
        options=ReviewOptions(assess="auto"),
    )
    assert work.assessment is None
    _git(repo, "checkout", "-qb", "feature")
    _git(repo, "commit", "-qam", "change x")
    based = review(
        cwd=str(repo),
        base="main",
        cfg=_cfg(),
        completer=_completer('{"findings":[]}'),
        options=ReviewOptions(assess="auto"),
    )
    assert based.assessment is not None


def test_review_caps_findings_by_severity(repo):
    lines = []
    for i in range(2, 8):
        lines.append(f"x{i} = {i}")
    (repo / "app.py").write_text("x = 1\n" + "\n".join(lines) + "\n")
    # Deterministic checks won't fire; use model findings on added lines.
    import json

    objs = []
    for i in range(2, 8):
        objs.append(
            {
                "file": "app.py",
                "line": i,
                "evidence": f"x{i} = {i}",
                "problem": f"p{i}",
                "severity": "minor" if i != 2 else "blocker",
                "category": "correctness",
                "confidence": "high",
            }
        )
    payload = json.dumps({"findings": objs})
    report = review(
        cwd=str(repo),
        cfg=_cfg(),
        completer=_completer(payload),
        options=ReviewOptions(assess="never", max_findings=2),
    )
    assert len(report.findings) == 2
    assert report.findings[0].severity == "blocker"


def test_review_no_changes_is_clean(repo):
    report = review(
        cwd=str(repo),
        cfg=_cfg(),
        completer=_completer('{"findings":[]}'),
        options=ReviewOptions(assess="never"),
    )
    assert report.findings == []
    assert "No evidence-backed findings" in report.to_markdown()


def test_review_truncated_diff_is_flagged(repo):
    (repo / "app.py").write_text("x = 1\n" + ("y = 2\n" * 50))
    report = review(
        cwd=str(repo),
        cfg=_cfg(),
        completer=_completer('{"findings":[]}'),
        options=ReviewOptions(assess="never", max_diff_bytes=100),
    )
    assert report.truncated is True


def test_intent_priority_and_no_invention(repo):
    d = DiffModel(
        target="worktree",
        repo_root=str(repo),
        ref="HEAD",
        meta={"title": "PR title", "body": "PR body"},
    )
    assert resolve_intent(d, explicit="user words").source == "user"
    pr = resolve_intent(d)
    assert pr.source == "pr-body"
    assert "PR title" in pr.text
    primary = DiffModel(target="worktree", repo_root=str(repo), ref="HEAD")
    assert resolve_intent(primary).available is False


def test_signals_are_facts():
    d = DiffModel(
        target="worktree",
        repo_root="/tmp",
        ref="HEAD",
        changed_lines=3,
        files=(
            FileDiff(
                path="src/auth/token.py",
                additions=2,
                hunks=(Hunk(1, 1, 1, 2, "@@ -1 +1,2 @@", ("+a", "+b")),),
            ),
            FileDiff(
                path="pyproject.toml",
                additions=1,
                hunks=(Hunk(1, 1, 1, 2, "@@ -1 +1,2 @@", ('+"requests>=2"',)),),
            ),
        ),
        skipped=(),
    )
    sig = compute_signals(d, Intent("add dependency", "user"))
    text = "\n".join(sig)
    assert "2 file(s)" in text
    assert "Security-sensitive paths" in text
    assert "Dependency/build files" in text
    assert "No test files" in text


def test_assess_system_prompt_is_advisory():
    assert "advisory" in ASSESS_SYSTEM.lower()
    assert "cannot block" in ASSESS_SYSTEM.lower()


def test_deterministic_checks_cover_exec_os_system_and_shell_true(repo):
    (repo / "app.py").write_text(
        'x = 1\nexec("x = 2")\nimport os\nos.system("ls")\n'
        'import subprocess\nsubprocess.run("ls", shell=True)\n'
    )
    report = review(
        cwd=str(repo),
        cfg=_cfg(),
        completer=_completer('{"findings":[]}'),
        options=ReviewOptions(assess="never"),
    )
    rules = {f.rule_id for f in report.findings}
    assert "security/dynamic-exec" in rules
    assert "security/os-system" in rules
    assert "security/subprocess-shell" in rules


def test_deterministic_checks_skip_malformed_python(repo):
    (repo / "app.py").write_text("def broken(:\n")
    report = review(
        cwd=str(repo),
        cfg=_cfg(),
        completer=_completer('{"findings":[]}'),
        options=ReviewOptions(assess="never"),
    )
    assert report.findings == []


def test_review_model_override_is_used(repo):
    (repo / "app.py").write_text("x = 2\n")
    seen = {}

    def call(cfg, *, system, user, max_tokens=4096):
        seen["model"] = cfg.model
        return '{"findings":[]}'

    review(
        cwd=str(repo),
        cfg=_cfg(),
        completer=call,
        options=ReviewOptions(assess="never", model="override"),
    )
    assert seen["model"] == "override"


def test_review_posts_pr_when_auto_and_credentials_work(repo, monkeypatch):
    (repo / "app.py").write_text('x = 1\ny = eval("1")\n')
    diff = DiffModel(
        target="pr:3",
        repo_root=str(repo),
        ref="PR #3",
        files=(
            FileDiff(
                path="app.py",
                hunks=(Hunk(1, 1, 1, 2, "@@ -1 +1,2 @@", (" x = 1", '+y = eval("1")')),),
            ),
        ),
        changed_lines=1,
    )
    import sk.review.engine as eng

    monkeypatch.setattr(eng, "post_review", lambda *a, **k: ("https://example.test/3", ""))
    report = review(
        pr=3,
        diff=diff,
        cfg=_cfg(),
        completer=_completer('{"findings":[]}'),
        options=ReviewOptions(assess="never"),
    )
    assert report.posted_url == "https://example.test/3"
    assert report.post_note == ""


def test_review_post_failure_does_not_lose_report(repo, monkeypatch):
    (repo / "app.py").write_text("x = 1\n")
    diff = DiffModel(target="pr:3", repo_root=str(repo), ref="PR #3", changed_lines=1)
    import sk.review.engine as eng

    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(eng, "post_review", boom)
    report = review(
        pr=3,
        diff=diff,
        cfg=_cfg(),
        completer=_completer('{"findings":[]}'),
        options=ReviewOptions(assess="never"),
    )
    assert report.posted_url == ""
    assert "posting failed" in report.post_note


def test_review_no_post_note(repo):
    (repo / "app.py").write_text("x = 1\n")
    diff = DiffModel(target="pr:3", repo_root=str(repo), ref="PR #3", changed_lines=1)
    report = review(
        pr=3,
        diff=diff,
        cfg=_cfg(),
        completer=_completer('{"findings":[]}'),
        options=ReviewOptions(assess="never", post="never"),
    )
    assert "disabled" in report.post_note


def test_should_assess_modes():
    from sk.review.engine import _should_assess

    assert _should_assess("never", "pr:1") is False
    assert _should_assess("always", "worktree") is True
    assert _should_assess("auto", "worktree") is False
    assert _should_assess("auto", "base:main") is True


def test_signals_cover_docs_migrations_config_and_truncation():
    d = DiffModel(
        target="worktree",
        repo_root="/tmp",
        ref="HEAD",
        changed_lines=600,
        truncated=True,
        files=(
            FileDiff(path="docs/guide.md", additions=1),
            FileDiff(path="db/migrations/001.py", additions=1),
            FileDiff(path="Dockerfile", additions=1),
        ),
        skipped=(),
    )
    text = "\n".join(compute_signals(d, Intent("", "inferred")))
    assert "No stated intent" in text
    assert "truncated" in text
    assert "Large diff" in text
    assert "migration" in text.lower()
    assert "Docs changed" in text
    assert "config" in text.lower()


def test_assessment_drops_questions_with_invented_evidence(repo):
    (repo / "app.py").write_text("x = 2\n")
    payload = (
        '{"stated_intent":"x","intent_source":"user","signals":[],'
        '"questions":[{"question":"Why?","evidence":"a line that is not in the diff"},'
        '{"question":"Grounded?","evidence":"x = 2"}],'
        '"risks":[],"verdict":"proceed","confidence":"low","assumptions":[]}'
    )
    report = review(
        cwd=str(repo),
        cfg=_cfg(),
        completer=_completer('{"findings":[]}', assessment_payload=payload),
        options=ReviewOptions(assess="always", intent="change x"),
    )
    assert report.assessment is not None
    assert [q.question for q in report.assessment.questions] == ["Grounded?"]


def test_review_verify_runs_for_reviewed_files(repo, monkeypatch):
    import sk.verify as verify_mod
    from sk.verify import VerificationResult

    (repo / "app.py").write_text("x = 2\n")
    diff = DiffModel(
        target="worktree",
        repo_root=str(repo),
        ref="HEAD",
        changed_lines=1,
        files=(FileDiff(path="app.py", status="M", additions=1),),
    )
    seen: dict = {}

    def fake_verify(files, **kwargs):
        seen.update({"files": files, **kwargs})
        return VerificationResult(status="fail", root=str(repo), files=tuple(files))

    monkeypatch.setattr(verify_mod, "verify_files", fake_verify)

    def approve(name, args):
        return True

    report = review(
        diff=diff,
        cfg=_cfg(),
        completer=_completer('{"findings":[]}'),
        options=ReviewOptions(assess="never", verify=True, trust_project=True),
        approve=approve,
    )

    assert seen["files"] == ["app.py"]
    assert seen["trust_project"] is True
    assert seen["approve"] is approve
    assert report.verification is not None
    assert report.verification["status"] == "fail"
    data = json.loads(report.to_json())
    assert data["verification"]["status"] == "fail"
    assert "### Verification" in report.to_markdown()
    assert "**Status: FAIL**" in report.to_markdown()


def test_review_without_verify_skips_checks(repo, monkeypatch):
    import sk.verify as verify_mod

    (repo / "app.py").write_text("x = 2\n")

    def boom(*args, **kwargs):
        raise AssertionError("verification must be opt-in")

    monkeypatch.setattr(verify_mod, "verify_files", boom)
    report = review(
        cwd=str(repo),
        cfg=_cfg(),
        completer=_completer('{"findings":[]}'),
        options=ReviewOptions(assess="never"),
    )
    assert report.verification is None
