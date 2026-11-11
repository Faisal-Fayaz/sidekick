"""Runner contract: statuses, trust, approval, changed filtering, audit."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import sk.store as store
from sk.check.config import CheckCommand, CheckPack
from sk.check.gitutil import changed_files
from sk.check.report import format_compact
from sk.check.runner import EXIT_CONFIG, EXIT_FAILED, EXIT_OK, run_checks


def _pack(*checks: CheckCommand, root: str = "/tmp/repo", trusted: bool = True) -> CheckPack:
    return CheckPack(
        root=root,
        present=True,
        trusted=trusted,
        checks=checks if trusted else (),
        declared=checks,
    )


def _shell(exit_code: int, output: str = "", command: str = "check") -> str:
    return f"$ {command}\n[exit {exit_code}]\n{output}"


def _dispatch(result: str, approved: bool = True, seen: dict | None = None):
    def call(name, args, approve, **kwargs):
        if seen is not None:
            seen.update({"name": name, "args": args, "approve": approve, **kwargs})
        return result, approved

    return call


def _run(pack: CheckPack, dispatch, *, approve=lambda name, args: True, **kwargs):
    return run_checks(
        pack=pack,
        approve=approve,
        dispatch=dispatch,
        session="check-test",
        **kwargs,
    )


def test_pass_with_structured_diagnostics():
    check = CheckCommand(id="lint", command="ruff check .", parser="ruff")
    report = _run(
        _pack(check),
        _dispatch(_shell(0, "src/a.py:1:1: F401 unused import\n")),
    )
    assert report.status == "pass"
    assert report.exit_code == EXIT_OK
    assert report.counts["pass"] == 1
    result = report.results[0]
    assert result.parsed is True
    assert result.approved is True
    assert result.diagnostics[0].file == "src/a.py"


def test_fail_with_structured_diagnostics():
    check = CheckCommand(id="typecheck", command="mypy src", parser="mypy")
    report = _run(
        _pack(check),
        _dispatch(_shell(1, "src/a.py:4: error: bad type  [assignment]\n")),
    )
    assert report.status == "fail"
    assert report.exit_code == EXIT_FAILED
    result = report.results[0]
    assert result.status == "fail"
    assert result.exit_code == 1
    assert result.parsed is True
    assert result.diagnostics[0].code == "assignment"


def test_timeout_is_unknown_never_pass():
    check = CheckCommand(id="test", command="pytest -q", parser="pytest")
    report = _run(_pack(check), _dispatch("Error: timed out after 5s", approved=True))
    assert report.status == "unknown"
    assert report.exit_code == EXIT_FAILED
    assert report.results[0].timed_out is True
    assert report.results[0].status == "unknown"


def test_missing_tool_is_unknown():
    check = CheckCommand(id="test", command="missing-tool", parser="raw")
    report = _run(
        _pack(check),
        _dispatch(_shell(127, "bash: line 1: missing-tool: command not found")),
    )
    assert report.status == "unknown"
    assert "missing tool" in report.results[0].note


def test_denied_by_user_is_unknown():
    check = CheckCommand(id="test", command="pytest -q", parser="pytest")
    report = _run(
        _pack(check),
        _dispatch("Denied by user: shell ... not executed.", approved=False),
    )
    assert report.status == "unknown"
    assert report.results[0].approved is False
    assert "not approved" in report.results[0].note


def test_hard_block_is_unknown():
    check = CheckCommand(id="test", command="rm -rf /", parser="raw")
    report = _run(
        _pack(check),
        _dispatch(
            "Error: blocked destructive command (refused even with approval).", approved=False
        ),
    )
    assert report.status == "unknown"
    assert "hard-block" in report.results[0].note


def test_output_overflow_is_unknown():
    check = CheckCommand(id="test", command="yes", parser="raw")
    report = _run(
        _pack(check),
        _dispatch(_shell(-9, "... [output cap reached — the process was killed]")),
    )
    assert report.status == "unknown"
    assert report.results[0].truncated is True
    assert "output cap" in report.results[0].note


def test_unparsed_failure_keeps_raw_output_and_stays_fail():
    check = CheckCommand(id="test", command="pytest -q", parser="pytest")
    report = _run(_pack(check), _dispatch(_shell(1, "something went wrong\n")))
    result = report.results[0]
    assert report.status == "fail"
    assert result.status == "fail"
    assert result.parsed is False
    assert "unparsed pytest output" in result.note
    assert "something went wrong" in result.raw


def test_raw_parser_is_always_unparsed_but_can_pass():
    check = CheckCommand(id="smoke", command="make", parser="raw")
    report = _run(_pack(check), _dispatch(_shell(0, "ok\n")))
    assert report.status == "pass"
    assert report.results[0].parsed is False


def test_missing_approval_callback_refuses_to_run():
    check = CheckCommand(id="test", command="pytest -q", parser="pytest")

    def boom(*args, **kwargs):
        raise AssertionError("dispatch must not be called without approval")

    report = _run(_pack(check), boom, approve=None)
    assert report.status == "unknown"
    assert "no approval callback" in report.results[0].note


def test_dispatch_receives_shell_args_and_approver():
    check = CheckCommand(id="lint", command="ruff check .", parser="ruff", timeout=33)
    seen: dict = {}
    report = _run(_pack(check), _dispatch(_shell(0, ""), seen=seen))
    assert report.status == "pass"
    assert seen["name"] == "shell"
    assert seen["args"] == {"cmd": "ruff check .", "timeout": 33, "cwd": "/tmp/repo"}
    assert seen["session"] == "check-test"
    assert seen["approve"]("shell", seen["args"]) is True


def test_changed_mode_filters_and_substitutes_files():
    lint = CheckCommand(
        id="lint",
        command="ruff check .",
        parser="ruff",
        applies_to=("**/*.py",),
        changed_command="ruff check {files}",
    )
    tests = CheckCommand(
        id="test",
        command="go test ./...",
        parser="go-test",
        applies_to=("**/*.go",),
    )
    seen: dict = {}

    def capture(name, args, approve, **kwargs):
        seen["args"] = args
        return _shell(0, ""), True

    report = run_checks(
        pack=_pack(lint, tests),
        files=("src/a.py", "README.md"),
        approve=lambda name, args: True,
        dispatch=capture,
    )
    assert report.changed is True
    assert report.targeted_files == ("src/a.py", "README.md")
    assert [result.id for result in report.results] == ["lint", "test"]
    assert report.results[0].command == "ruff check src/a.py"
    assert report.results[1].status == "skipped"
    assert seen["args"]["cmd"] == "ruff check src/a.py"


def test_changed_mode_with_no_matching_files_skips_all():
    check = CheckCommand(
        id="test",
        command="go test ./...",
        parser="go-test",
        applies_to=("**/*.go",),
    )
    report = _run(_pack(check), _dispatch(_shell(0, "")), files=("README.md",))
    assert report.status == "pass"
    assert report.counts["skipped"] == 1
    assert report.results[0].status == "skipped"


def test_empty_target_file_list_skips_without_dispatch():
    check = CheckCommand(id="test", command="pytest -q", parser="pytest")

    def boom(*args, **kwargs):
        raise AssertionError("nothing should run when no files changed")

    report = _run(_pack(check), boom, files=())
    assert report.note == "no changed files"
    assert report.results[0].status == "skipped"


def test_kinds_filter():
    lint = CheckCommand(id="lint", command="ruff check .", kind="lint")
    test = CheckCommand(id="test", command="pytest -q", kind="test", parser="pytest")
    report = _run(_pack(lint, test), _dispatch(_shell(0, "")), kinds=("lint",))
    assert [result.id for result in report.results] == ["lint"]


def test_untrusted_pack_is_unknown_and_never_dispatches():
    check = CheckCommand(id="test", command="pytest -q", parser="pytest")
    pack = CheckPack(
        root="/tmp/repo",
        present=True,
        trusted=False,
        checks=(),
        declared=(check,),
        warnings=("untrusted",),
    )

    def boom(*args, **kwargs):
        raise AssertionError("untrusted checks must not run")

    report = run_checks(pack=pack, approve=lambda name, args: True, dispatch=boom)
    assert report.status == "unknown"
    assert report.exit_code == EXIT_CONFIG
    assert report.note == "project checks are untrusted"
    assert report.trusted is False


def test_config_errors_are_unknown():
    pack = CheckPack(root="/tmp/repo", present=True, errors=("bad toml",))
    report = run_checks(pack=pack, approve=lambda name, args: True)
    assert report.status == "unknown"
    assert report.exit_code == EXIT_CONFIG
    assert report.config_errors == ("bad toml",)


def test_no_checks_is_empty_pass():
    report = run_checks(pack=CheckPack(root="/tmp/repo"), approve=lambda name, args: True)
    assert report.status == "pass"
    assert report.exit_code == EXIT_OK
    assert report.note == "no checks configured"


def test_report_json_and_compact_are_available():
    check = CheckCommand(id="lint", command="ruff check .", parser="ruff")
    report = _run(
        _pack(check),
        _dispatch(_shell(1, "src/a.py:2:3: F401 unused\n")),
    )
    data = json.loads(report.to_json())
    assert data["status"] == "fail"
    assert data["results"][0]["diagnostics"][0]["file"] == "src/a.py"
    text = format_compact(report)
    assert "FAIL" in text
    assert "src/a.py:2:3" in text
    assert "Summary: 1 fail" in text


def test_compact_marks_unparsed_raw_output():
    check = CheckCommand(id="test", command="pytest -q", parser="pytest")
    report = _run(_pack(check), _dispatch(_shell(1, "mystery failure\n")))
    text = format_compact(report)
    assert "raw (unparsed)" in text
    assert "mystery failure" in text


def test_default_dispatch_uses_the_gated_shell_path_and_audit(tmp_path, monkeypatch):
    if shutil.which("bash") is None:
        pytest.skip("needs bash")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    config = tmp_path / ".sidekick" / "checks.toml"
    config.parent.mkdir()
    config.write_text('schema = 1\n[checks]\nsmoke = "printf ok"\n')

    from sk.check.config import load_checks_pack

    pack = load_checks_pack(cwd=str(tmp_path), trust_project=True, env={})
    report = run_checks(
        pack=pack,
        approve=lambda name, args: True,
        session="audit-check",
        provider="ollama",
        host="",
    )
    assert report.status == "pass", format_compact(report)
    rows = store.list_tool_runs("audit-check")
    assert len(rows) == 1
    assert rows[0]["tool"] == "shell"
    assert rows[0]["approved"] == 1
    assert "printf ok" in rows[0]["target"]


def test_checks_run_from_the_project_root_not_the_invocation_directory(tmp_path, monkeypatch):
    if shutil.which("bash") is None or shutil.which("git") is None:
        pytest.skip("needs bash and git")
    repo = tmp_path / "repo"
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    (repo / ".sidekick").mkdir()
    (repo / ".sidekick" / "checks.toml").write_text('schema = 1\n[checks]\nwhere = "pwd"\n')
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(sub)

    from sk.check.config import load_checks_pack

    pack = load_checks_pack(cwd=str(sub), trust_project=True, env={})
    report = run_checks(pack=pack, approve=lambda name, args: True, session="cwd-check")
    assert report.status == "pass", format_compact(report)
    assert report.repo_root == str(repo)
    assert str(repo) in report.results[0].raw


def test_default_dispatch_logs_a_denied_check(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    config = tmp_path / ".sidekick" / "checks.toml"
    config.parent.mkdir()
    config.write_text('schema = 1\n[checks]\nsmoke = "printf ok"\n')

    from sk.check.config import load_checks_pack

    pack = load_checks_pack(cwd=str(tmp_path), trust_project=True, env={})
    report = run_checks(
        pack=pack,
        approve=lambda name, args: False,
        session="audit-denied",
    )
    assert report.status == "unknown"
    rows = store.list_tool_runs("audit-denied")
    assert len(rows) == 1
    assert rows[0]["approved"] == 0


def test_changed_files_includes_modified_and_untracked_but_not_deleted(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("needs git")
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str):
        result = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "keep.py").write_text("x = 1\n")
    (repo / "delete.py").write_text("y = 2\n")
    git("add", ".")
    git("commit", "-qm", "initial")

    (repo / "keep.py").write_text("x = 2\n")
    (repo / "delete.py").unlink()
    (repo / "new.py").write_text("z = 3\n")

    assert changed_files(str(repo)) == ("keep.py", "new.py")


def test_changed_files_keeps_non_ascii_and_space_names(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("needs git")
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str):
        result = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "base.py").write_text("x = 1\n")
    git("add", ".")
    git("commit", "-qm", "initial")

    (repo / "héllo wörld.py").write_text("y = 2\n")

    assert changed_files(str(repo)) == ("héllo wörld.py",)


def test_changed_files_falls_back_to_everything_on_an_unborn_branch(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("needs git")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "tracked.py").write_text("x = 1\n")
    (repo / "untracked.py").write_text("y = 2\n")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.py"], check=True)

    assert changed_files(str(repo)) == ("tracked.py", "untracked.py")
