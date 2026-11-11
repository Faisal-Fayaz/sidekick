"""Post-edit verification: syntax checks plus configured project checks (#423)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sk.check.config import CheckCommand
from sk.verify import (
    VerificationResult,
    _relative_paths,
    _status,
    _syntax_checks,
    verify_files,
)

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def _write_checks(root: Path, text: str) -> None:
    path = root / ".sidekick" / "checks.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _shell(exit_code: int, output: str = "") -> str:
    return f"$ check\n[exit {exit_code}]\n{output}"


def _approve(name, args):
    return True


def test_python_syntax_pass_runs_a_real_compile(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n")

    report = verify_files(["app.py"], cwd=str(repo), approve=_approve, session="verify-pass")

    assert report.status == "pass", report.compact()
    assert [result.id for result in report.results] == ["syntax-python"]
    assert report.results[0].kind == "syntax"
    assert report.repairable is False


def test_python_syntax_failure_is_a_failed_verification(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("def broken(:\n    pass\n")

    report = verify_files(["app.py"], cwd=str(repo), approve=_approve, session="verify-fail")

    assert report.status == "fail", report.compact()
    assert report.repairable is True
    result = report.results[0]
    assert result.status == "fail"
    assert result.exit_code not in (None, 0)
    assert "SyntaxError" in result.raw
    assert "SyntaxError" in report.compact()


def test_configured_ruff_failure_is_a_check_failure_not_a_tool_error(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("import os\n")
    _write_checks(
        repo,
        """
schema = 1
[[checks]]
id = "lint"
command = "printf 'app.py:1:1: F401 os imported but unused\\n'; exit 1"
kind = "lint"
parser = "ruff"
applies_to = ["**/*.py"]
""",
    )

    report = verify_files(
        ["app.py"],
        cwd=str(repo),
        trust_project=True,
        approve=_approve,
        session="verify-ruff",
    )

    assert report.status == "fail", report.compact()
    lint = [result for result in report.results if result.id == "lint"][0]
    assert lint.status == "fail"
    assert lint.parsed is True
    assert lint.diagnostics[0].file == "app.py"
    assert lint.diagnostics[0].code == "F401"
    assert "unparsed" not in lint.note
    assert "Error:" not in report.compact()


def test_typescript_layout_uses_the_configured_typecheck(tmp_path):
    repo = _repo(tmp_path)
    web = repo / "web" / "src"
    web.mkdir(parents=True)
    (web / "app.ts").write_text("const n: string = 1;\n")
    _write_checks(
        repo,
        """
schema = 1
[[checks]]
id = "typecheck"
command = "printf 'web/src/app.ts(1,7): error TS2322: Type number is not assignable to type string\\n'; exit 1"
kind = "typecheck"
parser = "tsc"
applies_to = ["web/**/*.ts"]
""",
    )

    report = verify_files(
        ["web/src/app.ts"],
        cwd=str(repo),
        trust_project=True,
        approve=_approve,
        session="verify-ts",
    )

    assert report.status == "fail", report.compact()
    assert [result.id for result in report.results] == ["typecheck"]
    diagnostic = report.results[0].diagnostics[0]
    assert diagnostic.file == "web/src/app.ts"
    assert diagnostic.line == 1
    assert diagnostic.code == "TS2322"


def test_missing_tool_is_unknown_never_pass(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("x = 1\n")
    _write_checks(
        repo,
        """
schema = 1
[checks]
lint = "sidekick-tool-that-does-not-exist-423"
""",
    )

    report = verify_files(
        ["app.py"],
        cwd=str(repo),
        trust_project=True,
        approve=_approve,
        session="verify-missing",
    )

    assert report.status == "unknown", report.compact()
    configured = [result for result in report.results if result.id == "lint"][0]
    assert configured.status == "unknown"
    assert "missing tool" in configured.note


def test_timeout_is_unknown_never_pass(tmp_path):
    repo = _repo(tmp_path)
    (repo / "web").mkdir()
    (repo / "web" / "app.ts").write_text("const x = 1;\n")
    _write_checks(
        repo,
        """
schema = 1
[[checks]]
id = "typecheck"
command = "tsc --noEmit"
kind = "typecheck"
parser = "tsc"
applies_to = ["web/**/*.ts"]
""",
    )

    def dispatch(name, args, approve, **kwargs):
        return "Error: timed out after 120s", True

    report = verify_files(
        ["web/app.ts"],
        cwd=str(repo),
        trust_project=True,
        approve=_approve,
        session="verify-timeout",
        dispatch=dispatch,
    )

    assert report.status == "unknown", report.compact()
    assert report.results[0].timed_out is True
    assert report.results[0].status == "unknown"
    assert report.repairable is False


def test_untrusted_checks_are_unknown_and_never_dispatched(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("x = 1\n")
    _write_checks(repo, 'schema = 1\n[checks]\nlint = "touch should-not-run"\n')
    calls: list[str] = []

    def dispatch(name, args, approve, **kwargs):
        calls.append(str(args.get("cmd", "")))
        return _shell(0, "(ok)"), True

    report = verify_files(
        ["app.py"],
        cwd=str(repo),
        trust_project=False,
        approve=_approve,
        session="verify-untrusted",
        dispatch=dispatch,
    )

    assert report.status == "unknown", report.compact()
    assert any("untrusted" in error for error in report.errors)
    assert not any("touch should-not-run" in command for command in calls)
    assert report.repairable is False


def test_no_approval_callback_runs_nothing(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("x = 1\n")
    calls = {"n": 0}

    def dispatch(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("verification must not dispatch without approval")

    report = verify_files(
        ["app.py"],
        cwd=str(repo),
        approve=None,
        session="verify-no-approval",
        dispatch=dispatch,
    )

    assert report.status == "unknown"
    assert calls["n"] == 0
    assert report.results[0].approved is False


def test_targeted_verification_runs_lint_and_typecheck_only(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("x = 1\n")
    _write_checks(
        repo,
        """
schema = 1
[[checks]]
id = "lint"
command = "printf 'app.py:1:1: F401 unused\\n'; exit 1"
kind = "lint"
parser = "ruff"
applies_to = ["**/*.py"]

[[checks]]
id = "typecheck"
command = "printf 'app.py:1: error: bad type  [assignment]\\n'; exit 1"
kind = "typecheck"
parser = "mypy"
applies_to = ["**/*.py"]

[[checks]]
id = "test"
command = "printf '1 passed in 0.01s\\n'"
kind = "test"
parser = "pytest"
applies_to = ["**/*.py"]
""",
    )

    targeted = verify_files(
        ["app.py"],
        cwd=str(repo),
        trust_project=True,
        approve=_approve,
        session="verify-targeted",
    )
    assert targeted.status == "fail", targeted.compact()
    assert [result.id for result in targeted.results] == ["syntax-python", "lint", "typecheck"]

    full = verify_files(
        ["app.py"],
        cwd=str(repo),
        trust_project=True,
        full=True,
        approve=_approve,
        session="verify-full",
    )
    assert full.status == "fail", full.compact()
    assert [result.id for result in full.results] == [
        "syntax-python",
        "lint",
        "typecheck",
        "test",
    ]
    assert full.full is True


def test_verification_result_without_checks_is_skipped(tmp_path):
    repo = _repo(tmp_path)
    report = verify_files(["README.md"], cwd=str(repo), approve=_approve)
    assert report.status == "skipped"
    assert report.results == ()


def test_syntax_checks_group_by_language_and_skip_missing_files(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("x = 1\n")
    (repo / "b.py").write_text("y = 2\n")
    (repo / "c.json").write_text("{}\n")

    checks = _syntax_checks(["a.py", "b.py", "c.json", "missing.py"], str(repo))

    by_id = {check.id: check for check in checks}
    assert set(by_id) == {"syntax-python", "syntax-json"}
    assert "a.py" in by_id["syntax-python"].command
    assert "b.py" in by_id["syntax-python"].command
    assert "missing.py" not in by_id["syntax-python"].command


def test_relative_paths_normalizes_from_a_subdirectory(tmp_path):
    repo = _repo(tmp_path)
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    (sub / "app.py").write_text("x = 1\n")

    assert _relative_paths(["app.py"], str(repo), str(sub)) == ("src/pkg/app.py",)
    assert _relative_paths([str(sub / "app.py")], str(repo), str(sub)) == ("src/pkg/app.py",)


def test_compact_keeps_the_tail_of_long_raw_output():
    from sk.check.runner import CheckResult

    raw = "\n".join(['  File "<string>", line 1'] * 20 + ["SyntaxError: invalid syntax"])
    report = VerificationResult(
        status="fail",
        results=(
            CheckResult(
                id="syntax-python",
                command="python -c ...",
                kind="syntax",
                parser="raw",
                status="fail",
                exit_code=1,
                duration=0.0,
                raw=raw,
            ),
        ),
    )

    compact = report.compact()
    assert "SyntaxError: invalid syntax" in compact
    assert compact.count('File "<string>"') == 7
    assert compact.rstrip().endswith("SyntaxError: invalid syntax")


def test_status_precedence_unknown_beats_fail():
    from sk.check.runner import CheckResult

    def result(status: str) -> CheckResult:
        return CheckResult(
            id=status,
            command="x",
            kind="lint",
            parser="raw",
            status=status,
            exit_code=0,
            duration=0.0,
        )

    assert _status((result("fail"), result("unknown")), ()) == "unknown"
    assert _status((result("pass"), result("fail")), ()) == "fail"
    assert _status((), ()) == "skipped"
    assert _status((result("pass"),), ("bad config",)) == "unknown"


def test_verify_turn_repairs_on_a_failed_check(tmp_path, monkeypatch):
    import sk.agent as agent
    import sk.verify as verify_mod

    seen: dict = {}

    def fake_verify(files, **kwargs):
        seen.update({"files": files, **kwargs})
        return VerificationResult(status="fail", files=tuple(files))

    monkeypatch.setattr(verify_mod, "verify_files", fake_verify)
    done, used, note = agent._verify_turn(
        ["app.py"],
        0,
        approve=_approve,
        session="turn-fail",
        trust_project_checks=True,
        verify_full=True,
    )

    assert (done, used) == (False, 1)
    assert "verification: fail" in note
    assert seen["trust_project"] is True
    assert seen["full"] is True
    assert seen["files"] == ["app.py"]


def test_verify_turn_reports_unknown_without_repair(tmp_path, monkeypatch):
    import sk.agent as agent
    import sk.verify as verify_mod

    monkeypatch.setattr(
        verify_mod,
        "verify_files",
        lambda files, **kwargs: VerificationResult(status="unknown", errors=("missing tool",)),
    )
    done, used, note = agent._verify_turn(["app.py"], 0, approve=_approve, session="turn-unknown")

    assert done is True
    assert used == 0
    assert "verification: unknown" in note
    assert "missing tool" in note


def test_verify_turn_skips_read_only_and_plan_modes(monkeypatch):
    import sk.agent as agent
    import sk.verify as verify_mod

    def boom(*args, **kwargs):
        raise AssertionError("must not verify")

    monkeypatch.setattr(verify_mod, "verify_files", boom)
    assert agent._verify_turn(["app.py"], 0, approve=_approve, read_only=True) == (True, 0, "")
    assert agent._verify_turn(["app.py"], 0, approve=_approve, plan_mode=True) == (True, 0, "")


def test_verify_full_requested_reads_the_environment(monkeypatch):
    import sk.agent as agent

    monkeypatch.delenv("SIDEKICK_VERIFY_FULL", raising=False)
    assert agent._verify_full_requested() is False
    monkeypatch.setenv("SIDEKICK_VERIFY_FULL", "1")
    assert agent._verify_full_requested() is True
    monkeypatch.setenv("SIDEKICK_VERIFY_FULL", "yes")
    assert agent._verify_full_requested() is False


def test_syntax_command_uses_the_running_interpreter(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("x = 1\n")
    checks = _syntax_checks(["app.py"], str(repo))
    assert isinstance(checks[0], CheckCommand)
    assert "tokenize.open" in checks[0].command
    assert "py_compile" not in checks[0].command
