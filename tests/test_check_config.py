"""Checks config: trust gate, both TOML shapes, validation, inference, discovery."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from sk.check.config import (
    CheckCommand,
    infer_kind,
    infer_parser,
    load_checks_pack,
)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".sidekick" / "checks.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _load(tmp_path: Path, *, trust: bool = True, env: dict[str, str] | None = None):
    return load_checks_pack(cwd=str(tmp_path), trust_project=trust, env=env or {})


def test_missing_file_is_not_an_error(tmp_path: Path):
    pack = _load(tmp_path)
    assert pack.present is False
    assert pack.trusted is True
    assert pack.checks == ()
    assert pack.errors == ()
    assert pack.warnings == ()


def test_simple_map_loads_and_infers_metadata(tmp_path: Path):
    _write(
        tmp_path,
        """
schema = 1

[checks]
lint = "ruff check ."
test = "pytest -q"
""",
    )
    pack = _load(tmp_path)
    assert pack.errors == ()
    assert pack.trusted is True
    assert [check.id for check in pack.checks] == ["lint", "test"]
    lint = pack.checks[0]
    assert lint.command == "ruff check ."
    assert lint.effective_parser == "ruff"
    assert lint.kind == "lint"
    assert lint.timeout == 120
    assert lint.applies_to == ("**/*.py", "**/*.pyi")
    assert pack.checks[1].effective_parser == "pytest"
    assert pack.checks[1].kind == "test"


def test_untrusted_project_loads_nothing_but_declares_checks(tmp_path: Path):
    _write(
        tmp_path,
        """
schema = 1
[checks]
test = "pytest -q"
""",
    )
    pack = _load(tmp_path, trust=False)
    assert pack.present is True
    assert pack.trusted is False
    assert pack.checks == ()
    assert len(pack.declared) == 1
    assert pack.errors == ()
    assert any("untrusted" in warning for warning in pack.warnings)
    assert any("SIDEKICK_TRUST_REPO" in warning for warning in pack.warnings)


def test_env_trust_loads_the_file(tmp_path: Path):
    _write(tmp_path, 'schema = 1\n[checks]\ntest = "pytest -q"\n')
    pack = _load(tmp_path, trust=False, env={"SIDEKICK_TRUST_REPO": "1"})
    assert pack.trusted is True
    assert [check.id for check in pack.checks] == ["test"]


def test_trust_flag_loads_the_file(tmp_path: Path):
    _write(tmp_path, 'schema = 1\n[checks]\ntest = "pytest -q"\n')
    pack = _load(tmp_path, trust=True)
    assert pack.trusted is True
    assert [check.id for check in pack.checks] == ["test"]


def test_table_entry_supports_changed_timeout_and_globs(tmp_path: Path):
    _write(
        tmp_path,
        """
schema = 1

[checks.typecheck]
command = "mypy src"
parser = "mypy"
timeout = 60
applies_to = ["src/**/*.py"]
changed = "mypy {files}"
""",
    )
    pack = _load(tmp_path)
    assert pack.errors == ()
    check = pack.checks[0]
    assert check.id == "typecheck"
    assert check.kind == "typecheck"
    assert check.effective_parser == "mypy"
    assert check.timeout == 60
    assert check.applies_to == ("src/**/*.py",)
    assert check.changed_command == "mypy {files}"
    assert check.with_files(["src/a.py"]) == "mypy {files}"


def test_array_form_loads(tmp_path: Path):
    _write(
        tmp_path,
        """
schema = 1

[[checks]]
id = "tests"
command = "go test ./..."
parser = "go-test"
""",
    )
    pack = _load(tmp_path)
    assert pack.errors == ()
    assert pack.checks[0].id == "tests"
    assert pack.checks[0].effective_parser == "go-test"
    assert pack.checks[0].applies_to == ("**/*.go",)


def test_defaults_are_safe_for_unknown_commands(tmp_path: Path):
    _write(tmp_path, 'schema = 1\n[checks]\nbuild = "make"\n')
    pack = _load(tmp_path)
    check = pack.checks[0]
    assert check.effective_parser == "raw"
    assert check.kind == "build"
    assert check.applies_to == ("**/*",)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("uv run pytest -q", "pytest"),
        ("python -m pytest", "pytest"),
        ("ruff check .", "ruff"),
        ("uv run mypy src", "mypy"),
        ("npx eslint src", "eslint"),
        ("npx tsc --noEmit", "tsc"),
        ("go test ./...", "go-test"),
        ("make test", "raw"),
        ("npm test", "raw"),
    ],
)
def test_parser_inference(command: str, expected: str):
    assert infer_parser(command) == expected


@pytest.mark.parametrize(
    ("command", "check_id", "parser", "expected"),
    [
        ("ruff check .", "lint", "ruff", "lint"),
        ("ruff format --check .", "format", "ruff", "format"),
        ("mypy src", "typecheck", "mypy", "typecheck"),
        ("pytest -q", "test", "pytest", "test"),
        ("go test ./...", "tests", "go-test", "test"),
        ("make", "build", "raw", "build"),
        ("./custom", "verify", "raw", "other"),
    ],
)
def test_kind_inference(command: str, check_id: str, parser: str, expected: str):
    assert infer_kind(command, check_id, parser) == expected


def test_schema_is_required(tmp_path: Path):
    _write(tmp_path, '[checks]\ntest = "pytest -q"\n')
    pack = _load(tmp_path)
    assert any("schema is required" in error for error in pack.errors)
    assert pack.checks == ()


def test_unsupported_schema_is_an_error(tmp_path: Path):
    _write(tmp_path, 'schema = 2\n[checks]\ntest = "pytest -q"\n')
    pack = _load(tmp_path)
    assert any("unsupported schema" in error for error in pack.errors)


def test_unknown_top_level_key_is_an_error(tmp_path: Path):
    _write(tmp_path, 'schema = 1\nsurprise = true\n[checks]\ntest = "pytest -q"\n')
    pack = _load(tmp_path)
    assert any("unknown key 'surprise'" in error for error in pack.errors)


def test_unknown_check_key_is_an_error(tmp_path: Path):
    _write(tmp_path, 'schema = 1\n[checks.test]\ncommand = "pytest"\ncolour = "red"\n')
    pack = _load(tmp_path)
    assert any("unknown key 'colour'" in error for error in pack.errors)


def test_duplicate_ids_are_an_error(tmp_path: Path):
    _write(
        tmp_path,
        """
schema = 1
[[checks]]
id = "test"
command = "pytest"
[[checks]]
id = "test"
command = "pytest -q"
""",
    )
    pack = _load(tmp_path)
    assert any("duplicate check id" in error for error in pack.errors)


def test_invalid_parser_is_an_error(tmp_path: Path):
    _write(tmp_path, 'schema = 1\n[checks.test]\ncommand = "pytest"\nparser = "wat"\n')
    pack = _load(tmp_path)
    assert any("parser must be one of" in error for error in pack.errors)


def test_invalid_timeout_is_an_error(tmp_path: Path):
    _write(tmp_path, 'schema = 1\n[checks.test]\ncommand = "pytest"\ntimeout = 0\n')
    pack = _load(tmp_path)
    assert any("timeout must be a number" in error for error in pack.errors)


def test_timeout_below_the_shell_floor_is_an_error(tmp_path: Path):
    _write(tmp_path, 'schema = 1\n[checks.test]\ncommand = "pytest"\ntimeout = 1\n')
    pack = _load(tmp_path)
    assert any("between 5 and 120" in error for error in pack.errors)


def test_timeout_at_the_shell_floor_is_accepted(tmp_path: Path):
    _write(tmp_path, 'schema = 1\n[checks.test]\ncommand = "pytest"\ntimeout = 5\n')
    pack = _load(tmp_path)
    assert pack.errors == ()
    assert pack.checks[0].timeout == 5


def test_invalid_glob_is_an_error(tmp_path: Path):
    _write(
        tmp_path,
        'schema = 1\n[checks.test]\ncommand = "pytest"\napplies_to = ["../outside"]\n',
    )
    pack = _load(tmp_path)
    assert any("may not traverse" in error for error in pack.errors)


def test_empty_command_is_an_error(tmp_path: Path):
    _write(tmp_path, 'schema = 1\n[checks]\ntest = "   "\n')
    pack = _load(tmp_path)
    assert any("must not be empty" in error for error in pack.errors)


def test_command_over_shell_limit_is_an_error(tmp_path: Path):
    _write(tmp_path, f'schema = 1\n[checks]\ntest = "{"x" * 2001}"\n')
    pack = _load(tmp_path)
    assert any("shell limit" in error for error in pack.errors)


def test_no_checks_is_a_warning_not_an_error(tmp_path: Path):
    _write(tmp_path, "schema = 1\n")
    pack = _load(tmp_path)
    assert pack.errors == ()
    assert pack.checks == ()
    assert any("no checks configured" in warning for warning in pack.warnings)


def test_explain_lists_loaded_checks(tmp_path: Path):
    _write(tmp_path, 'schema = 1\n[checks]\ntest = "pytest -q"\n')
    text = _load(tmp_path).explain()
    assert "pytest -q" in text
    assert "trusted: yes" in text
    assert "declared: 1" in text


def test_discovery_is_scoped_to_the_git_root(tmp_path: Path):
    if shutil.which("git") is None:
        pytest.skip("needs git")
    repo = tmp_path / "repo"
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    (repo / ".sidekick").mkdir()
    (repo / ".sidekick" / "checks.toml").write_text('schema = 1\n[checks]\ntest = "pytest -q"\n')
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    pack = load_checks_pack(cwd=str(sub), trust_project=True, env={})
    assert Path(pack.root) == repo
    assert pack.path == repo / ".sidekick" / "checks.toml"
    assert [check.id for check in pack.checks] == ["test"]


def test_check_command_with_files_is_unchanged_by_default(tmp_path: Path):
    check = CheckCommand(id="lint", command="ruff check .")
    assert check.with_files(["src/a.py"]) == "ruff check ."
