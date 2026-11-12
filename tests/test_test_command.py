"""`sk test`: the project's tests, through the check pipeline (#452).

Same machinery as `sk check` — same approval gate, same parsers, same exit
codes. What is tested here is the scope: the project's own declaration wins,
detection is the fallback, and a missing test runner is never a pass.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sk.check.testcmd import detect_test_command
from sk.cli import app
from sk.config import Config


def _cfg():
    return Config(provider="ollama", model="fake", base_url="http://x/v1", api_key="x")


# --- detection -------------------------------------------------------------


def test_detects_a_makefile_target(tmp_path):
    (tmp_path / "Makefile").write_text("test:\n\tpytest -q\n")
    assert detect_test_command(tmp_path) == "make test"


def test_a_makefile_without_a_test_target_falls_through(tmp_path):
    (tmp_path / "Makefile").write_text("build:\n\tpython -m build\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert detect_test_command(tmp_path) == "pytest -q"


def test_detects_an_npm_script(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    assert detect_test_command(tmp_path) == "npm test --silent"


def test_a_package_json_without_a_test_script_is_not_a_test_project(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "tsc"}}))
    assert detect_test_command(tmp_path) == ""


def test_detects_cargo_and_go(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
    assert detect_test_command(tmp_path) == "cargo test"
    other = tmp_path / "go"
    other.mkdir()
    (other / "go.mod").write_text("module x\n")
    assert detect_test_command(other) == "go test ./..."


def test_detects_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert detect_test_command(tmp_path) == "pytest -q"


def test_detects_python_by_convention(tmp_path):
    """No config file, but the convention is unmistakable."""
    project = tmp_path / "proj"
    (project / "tests").mkdir(parents=True)
    assert detect_test_command(project) == "pytest -q"


def test_nothing_to_detect(tmp_path):
    assert detect_test_command(tmp_path) == ""


def test_detection_never_invents_a_command(tmp_path):
    """The command comes from a file in the project, not from the model's head."""
    (tmp_path / "README.md").write_text("run `make check` to test\n")
    assert detect_test_command(tmp_path) == ""


# --- the command -----------------------------------------------------------


def _stdout(result) -> str:
    """The data channel: stdout only.

    The approval notices are human-facing and go to stderr, so a report can be
    piped into a parser. The test runner mixes the two streams into
    `result.output`, so the JSON has to be read from stdout.
    """
    return getattr(result, "stdout", None) or result.output


def _run(args, cwd: Path | None = None):
    runner = CliRunner()
    if cwd is not None:
        import os

        previous = os.getcwd()
        os.chdir(cwd)
        try:
            return runner.invoke(app, ["test", *args])
        finally:
            os.chdir(previous)
    return runner.invoke(app, ["test", *args])


def test_the_projects_declared_test_check_is_used(tmp_path, monkeypatch):
    """A declared check wins over detection."""
    import sk.check

    seen = {}

    def fake_load(**kwargs):
        from sk.check.config import CheckCommand, CheckPack

        pack = CheckPack(
            root=str(tmp_path),
            present=True,
            trusted=True,
            checks=(CheckCommand(id="unit", command="pytest -q", kind="test"),),
            declared=(CheckCommand(id="unit", command="pytest -q", kind="test"),),
        )
        seen["load"] = kwargs
        return pack

    def fake_run(**kwargs):
        seen["run"] = kwargs
        from sk.check.runner import CheckReport, CheckResult

        return CheckReport(
            results=(
                CheckResult(
                    id="unit",
                    command="pytest -q",
                    kind="test",
                    parser="pytest",
                    status="pass",
                    exit_code=0,
                    duration=0.1,
                ),
            ),
            repo_root=str(tmp_path),
            trusted=True,
        )

    monkeypatch.setattr(sk.check, "load_checks_pack", fake_load)
    monkeypatch.setattr(sk.check, "run_checks", fake_run)

    result = _run(["--json"], cwd=tmp_path)
    assert result.exit_code == 0, result.output
    assert seen["run"]["kinds"] == ("test",)
    assert json.loads(_stdout(result))["status"] == "pass"


def test_a_detected_command_runs_when_nothing_is_declared(tmp_path, monkeypatch):
    import sk.check

    seen = {}

    def fake_load(**kwargs):
        from sk.check.config import CheckPack

        return CheckPack(root=str(tmp_path), present=False, trusted=True)

    def fake_run(**kwargs):
        seen["run"] = kwargs
        from sk.check.runner import CheckReport, CheckResult

        return CheckReport(
            results=(
                CheckResult(
                    id="test",
                    command="pytest -q",
                    kind="test",
                    parser="pytest",
                    status="pass",
                    exit_code=0,
                    duration=0.1,
                ),
            ),
            repo_root=str(tmp_path),
            trusted=True,
        )

    monkeypatch.setattr(sk.check, "load_checks_pack", fake_load)
    monkeypatch.setattr(sk.check, "run_checks", fake_run)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")

    result = _run(["--json"], cwd=tmp_path)
    assert result.exit_code == 0, result.output
    assert seen["run"]["pack"].checks[0].command == "pytest -q"


def test_no_test_command_is_a_config_error(tmp_path, monkeypatch):
    import sk.check

    def fake_load(**kwargs):
        from sk.check.config import CheckPack

        return CheckPack(root=str(tmp_path), present=False, trusted=True)

    monkeypatch.setattr(sk.check, "load_checks_pack", fake_load)

    result = _run([], cwd=tmp_path)
    assert result.exit_code == 2, result.output
    assert "No test command found" in result.output


def test_untrusted_project_checks_are_refused(tmp_path, monkeypatch):
    import sk.check

    def fake_load(**kwargs):
        from sk.check.config import CheckCommand, CheckPack

        check = CheckCommand(id="unit", command="pytest -q", kind="test")
        return CheckPack(
            root=str(tmp_path), present=True, trusted=False, checks=(), declared=(check,)
        )

    monkeypatch.setattr(sk.check, "load_checks_pack", fake_load)

    result = _run([], cwd=tmp_path)
    assert result.exit_code == 2, result.output
    assert "untrusted" in result.output.lower()


def test_a_missing_test_runner_is_unknown_not_a_pass(tmp_path):
    """The runner cannot be found: the verdict is unknown, and the exit code
    says so. A missing runner must never read as a green suite."""
    result = _run(
        ["--command", "sidekick-no-such-test-runner-xyz", "--yes", "--json"],
        cwd=tmp_path,
    )
    assert result.exit_code == 1, result.output
    report = json.loads(_stdout(result))
    assert report["status"] == "unknown"
    assert report["ok"] is False


def test_a_failing_suite_exits_one_with_diagnostics(tmp_path):
    result = _run(
        ["--command", 'python3 -c "import sys; sys.exit(1)"', "--yes", "--json"],
        cwd=tmp_path,
    )
    assert result.exit_code == 1, result.output
    report = json.loads(_stdout(result))
    assert report["status"] == "fail"
    assert report["ok"] is False


def test_the_command_goes_through_the_approval_gate(tmp_path, monkeypatch):
    """`--yes` approves; without it the approver decides."""
    import sk.check

    seen = {}

    def fake_load(**kwargs):
        from sk.check.config import CheckPack

        return CheckPack(root=str(tmp_path), present=False, trusted=True)

    def fake_run(**kwargs):
        seen["approve"] = kwargs.get("approve")
        from sk.check.runner import CheckReport

        return CheckReport(repo_root=str(tmp_path), trusted=True)

    monkeypatch.setattr(sk.check, "load_checks_pack", fake_load)
    monkeypatch.setattr(sk.check, "run_checks", fake_run)

    _run(["--command", "pytest -q"], cwd=tmp_path)
    assert callable(seen["approve"]), "the command ran without an approver"
