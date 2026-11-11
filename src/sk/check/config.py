"""Load and validate ``.sidekick/checks.toml``.

The file is a project artifact, so it is untrusted by default. Two explicit
opt-ins load it: ``--trust-project-checks`` from the command line, or the
existing ``SIDEKICK_TRUST_REPO=1`` environment variable. Trust only decides
whether commands are *loaded*; every loaded command still goes through the
normal shell approval gate and audit ledger.

Two TOML shapes are accepted::

    [checks]
    lint = "ruff check ."

    [checks.typecheck]
    command = "mypy src"
    timeout = 60

and the equivalent array form::

    [[checks]]
    id = "lint"
    command = "ruff check ."
    changed = "ruff check {files}"

Unknown keys are errors, not silently ignored. A malformed file blocks all
execution, because a half-understood check set is not evidence.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..custom_commands import TRUST_REPO_ENV
from ..review.pack import _glob_error
from .gitutil import project_root

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.12+ is required
    import tomli as tomllib  # type: ignore

SCHEMA_VERSION = 1
CHECK_DIR = ".sidekick"
CHECK_FILENAME = "checks.toml"
DEFAULT_TIMEOUT = 120.0
# The shell tool clamps every timeout to 5–120 seconds; config validation uses
# the same bounds so an accepted value is the value that actually runs.
MIN_TIMEOUT = 5.0
MAX_TIMEOUT = 120.0
MAX_COMMAND = 2000

KINDS = ("lint", "typecheck", "test", "build", "format", "other")
PARSERS = ("auto", "pytest", "ruff", "mypy", "eslint", "tsc", "go-test", "raw")

_TOP_KEYS = frozenset({"schema", "checks"})
_CHECK_KEYS = frozenset({"id", "command", "kind", "parser", "timeout", "applies_to", "changed"})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_PARSER_ALIASES = {
    "auto": "auto",
    "pytest": "pytest",
    "py-test": "pytest",
    "ruff": "ruff",
    "mypy": "mypy",
    "eslint": "eslint",
    "tsc": "tsc",
    "typescript": "tsc",
    "go": "go-test",
    "gotest": "go-test",
    "go-test": "go-test",
    "go_test": "go-test",
    "raw": "raw",
}
_KIND_ALIASES = {
    "lint": "lint",
    "linting": "lint",
    "type": "typecheck",
    "types": "typecheck",
    "typecheck": "typecheck",
    "type-check": "typecheck",
    "test": "test",
    "tests": "test",
    "build": "build",
    "format": "format",
    "fmt": "format",
    "other": "other",
}


@dataclass(frozen=True)
class CheckCommand:
    """One configured check. ``parser='auto'`` is resolved from the command."""

    id: str
    command: str
    kind: str = "other"
    parser: str = "auto"
    timeout: float = DEFAULT_TIMEOUT
    applies_to: tuple[str, ...] = ("**/*",)
    changed_command: str = ""
    source: str = "project"

    @property
    def effective_parser(self) -> str:
        return infer_parser(self.command, self.id) if self.parser == "auto" else self.parser

    def with_files(self, files: Iterable[str]) -> str:
        """Return the command to run for ``--changed``.

        A configured ``changed`` template gets shell-quoted paths in place of
        ``{files}``. Without a template the normal command runs unchanged; that
        is the right default for tools that already read the repository.
        """
        if not self.changed_command:
            return self.command
        return self.changed_command


@dataclass(frozen=True)
class CheckPack:
    """A parsed checks file. ``checks`` is empty unless the project was trusted."""

    schema: int = SCHEMA_VERSION
    root: str = ""
    path: Path | None = None
    present: bool = False
    trusted: bool = True
    checks: tuple[CheckCommand, ...] = ()
    declared: tuple[CheckCommand, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def check(self) -> list[tuple[str, str]]:
        """(level, message) issues for ``sk check --check-config``."""
        out: list[tuple[str, str]] = [("error", error) for error in self.errors]
        out.extend(("warning", warning) for warning in self.warnings)
        return out

    def explain(self) -> str:
        lines = [f"Checks config · schema {self.schema}"]
        if not self.present or self.path is None:
            lines.append("path: (not found)")
            lines.append("No project checks configured.")
            return "\n".join(lines)
        lines.append(f"path: {self.path}")
        lines.append(f"trusted: {'yes' if self.trusted else 'no'}")
        lines.append(f"declared: {len(self.declared)}")
        lines.append(f"loaded: {len(self.checks)}")
        for check in self.checks:
            lines.append(
                f"  {check.id} [{check.kind}/{check.effective_parser}] "
                f"timeout={check.timeout:g}s applies_to={', '.join(check.applies_to)}"
            )
            lines.append(f"    {check.command}")
            if check.changed_command:
                lines.append(f"    changed: {check.changed_command}")
        return "\n".join(lines)


def normalize_parser(value: str) -> str:
    key = str(value or "").strip().lower().replace(" ", "-")
    return _PARSER_ALIASES.get(key, "")


def normalize_kind(value: str) -> str:
    key = str(value or "").strip().lower().replace(" ", "-")
    return _KIND_ALIASES.get(key, "")


def infer_parser(command: str, check_id: str = "") -> str:
    """Pick a parser from the command and id. Unknown commands fall back to raw."""
    hay = f"{check_id} {command}".lower()
    if re.search(r"\bgo\s+test\b", hay):
        return "go-test"
    if "pytest" in hay or "py.test" in hay:
        return "pytest"
    if re.search(r"\bruff\b", hay):
        return "ruff"
    if re.search(r"\bmypy\b", hay):
        return "mypy"
    if re.search(r"\beslint\b", hay):
        return "eslint"
    if re.search(r"\btsc\b", hay) or "typescript" in hay:
        return "tsc"
    return "raw"


def infer_kind(command: str, check_id: str, parser: str) -> str:
    hay = f"{check_id} {command}".lower()
    if "format" in hay or "fmt" in hay:
        return "format"
    if "lint" in hay or parser in ("ruff", "eslint"):
        return "lint"
    if "type" in hay or parser in ("mypy", "tsc"):
        return "typecheck"
    if "test" in hay or parser in ("pytest", "go-test"):
        return "test"
    if "build" in hay:
        return "build"
    return "other"


def default_applies_to(parser: str) -> tuple[str, ...]:
    if parser in ("pytest", "ruff", "mypy"):
        return ("**/*.py", "**/*.pyi")
    if parser == "eslint":
        return (
            "**/*.js",
            "**/*.jsx",
            "**/*.ts",
            "**/*.tsx",
            "**/*.mjs",
            "**/*.cjs",
            "**/*.vue",
        )
    if parser == "tsc":
        return ("**/*.ts", "**/*.tsx")
    if parser == "go-test":
        return ("**/*.go",)
    return ("**/*",)


def load_checks_pack(
    *,
    cwd: str = "",
    trust_project: bool = False,
    path: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> CheckPack:
    """Load the checks file for ``cwd``. Never executes anything."""
    root = project_root(cwd)
    config_path = Path(path) if path is not None else Path(root) / CHECK_DIR / CHECK_FILENAME
    environment = env if env is not None else os.environ
    trusted = bool(trust_project) or str(environment.get(TRUST_REPO_ENV, "")).strip() == "1"

    if not config_path.is_file():
        return CheckPack(root=root, path=None, present=False, trusted=True)

    try:
        raw = tomllib.loads(config_path.read_text(errors="replace"))
    except Exception as e:
        return CheckPack(
            root=root,
            path=config_path,
            present=True,
            trusted=trusted,
            errors=(f"{config_path}: {e}",),
        )
    if not isinstance(raw, dict):
        return CheckPack(
            root=root,
            path=config_path,
            present=True,
            trusted=trusted,
            errors=(f"{config_path}: top level must be a table",),
        )

    errors: list[str] = []
    warnings: list[str] = []
    for key in raw:
        if key not in _TOP_KEYS:
            errors.append(f"{config_path}: unknown key {key!r}")

    schema = raw.get("schema")
    if schema is None:
        errors.append(f"{config_path}: schema is required")
    elif isinstance(schema, bool) or not isinstance(schema, int):
        errors.append(f"{config_path}: schema must be an integer")
    elif schema != SCHEMA_VERSION:
        errors.append(f"{config_path}: unsupported schema {schema!r}; expected {SCHEMA_VERSION}")

    declared, check_errors = _parse_checks(raw.get("checks"), config_path)
    errors.extend(check_errors)

    if not check_errors and not declared:
        warnings.append(f"{config_path}: no checks configured")

    if not trusted:
        warnings.append(
            f"{config_path}: project checks ignored — untrusted by default "
            "(pass --trust-project-checks or set SIDEKICK_TRUST_REPO=1)"
        )

    effective = tuple(declared) if trusted and not errors else ()
    return CheckPack(
        root=root,
        path=config_path,
        present=True,
        trusted=trusted,
        checks=effective,
        declared=tuple(declared),
        warnings=tuple(dict.fromkeys(warnings)),
        errors=tuple(dict.fromkeys(errors)),
    )


def _parse_checks(raw: Any, path: Path) -> tuple[list[CheckCommand], list[str]]:
    if raw is None:
        return [], []
    entries: list[tuple[str, Any]] = []
    errors: list[str] = []

    if isinstance(raw, list):
        for index, item in enumerate(raw, 1):
            if not isinstance(item, dict):
                errors.append(f"{path}: checks[{index}] must be a table")
                continue
            check_id = item.get("id")
            if not isinstance(check_id, str) or not check_id.strip():
                errors.append(f"{path}: checks[{index}].id is required")
                continue
            entries.append((check_id.strip(), item))
    elif isinstance(raw, dict):
        for key, item in raw.items():
            check_id = str(key).strip()
            if not _ID.fullmatch(check_id):
                errors.append(f"{path}: invalid check id {key!r}")
                continue
            entries.append((check_id, item))
    else:
        return [], [f"{path}: [checks] must be a table or an array of tables"]

    seen: set[str] = set()
    out: list[CheckCommand] = []
    for check_id, item in entries:
        if check_id in seen:
            errors.append(f"{path}: duplicate check id {check_id!r}")
            continue
        seen.add(check_id)
        if isinstance(item, str):
            command, item_errors = _parse_command_value(item, check_id, path)
            if item_errors:
                errors.extend(item_errors)
                continue
            assert command is not None
            parser = infer_parser(command, check_id)
            kind = infer_kind(command, check_id, parser)
            out.append(
                CheckCommand(
                    id=check_id,
                    command=command,
                    kind=kind,
                    parser="auto",
                    timeout=DEFAULT_TIMEOUT,
                    applies_to=default_applies_to(parser),
                )
            )
            continue
        if not isinstance(item, dict):
            errors.append(f"{path}: check {check_id!r} must be a string or a table")
            continue
        check, check_errors = _parse_check_table(item, check_id, path)
        errors.extend(check_errors)
        if check is not None:
            out.append(check)
    return out, errors


def _parse_command_value(raw: Any, check_id: str, path: Path) -> tuple[str | None, list[str]]:
    if not isinstance(raw, str):
        return None, [f"{path}: check {check_id!r} command must be a string"]
    command = raw.strip()
    if not command:
        return None, [f"{path}: check {check_id!r} command must not be empty"]
    if len(command) > MAX_COMMAND:
        return None, [
            f"{path}: check {check_id!r} command is {len(command)} chars; "
            f"the shell limit is {MAX_COMMAND}"
        ]
    if "\x00" in command:
        return None, [f"{path}: check {check_id!r} command contains a NUL byte"]
    return command, []


def _parse_check_table(
    item: dict[str, Any], check_id: str, path: Path
) -> tuple[CheckCommand | None, list[str]]:
    errors: list[str] = []
    for key in item:
        if key not in _CHECK_KEYS:
            errors.append(f"{path}: check {check_id!r}: unknown key {key!r}")

    raw_id = item.get("id", check_id)
    if not isinstance(raw_id, str) or not raw_id.strip():
        errors.append(f"{path}: check {check_id!r}: id must be a non-empty string")
    elif raw_id.strip() != check_id:
        errors.append(f"{path}: check {check_id!r}: id {raw_id!r} does not match its key")
    if not _ID.fullmatch(check_id):
        errors.append(f"{path}: invalid check id {check_id!r}")

    command, command_errors = _parse_command_value(item.get("command"), check_id, path)
    errors.extend(command_errors)

    parser = "auto"
    if "parser" in item:
        parser = normalize_parser(str(item["parser"]))
        if not parser:
            errors.append(f"{path}: check {check_id!r}: parser must be one of {', '.join(PARSERS)}")
            parser = "auto"

    timeout = DEFAULT_TIMEOUT
    if "timeout" in item:
        raw_timeout = item["timeout"]
        try:
            if isinstance(raw_timeout, bool):
                raise ValueError
            timeout = float(raw_timeout)
            if not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(
                f"{path}: check {check_id!r}: timeout must be a number between "
                f"{MIN_TIMEOUT:g} and {MAX_TIMEOUT:g}"
            )
            timeout = DEFAULT_TIMEOUT

    raw_applies = item.get("applies_to")
    applies_to: tuple[str, ...] = ()
    if raw_applies is not None:
        if not isinstance(raw_applies, list) or not all(isinstance(x, str) for x in raw_applies):
            errors.append(
                f"{path}: check {check_id!r}: applies_to must be an array of glob strings"
            )
        else:
            patterns = tuple(x.strip() for x in raw_applies if x.strip())
            if not patterns:
                errors.append(f"{path}: check {check_id!r}: applies_to must not be empty")
            else:
                for pattern in patterns:
                    error = _glob_error(pattern)
                    if error:
                        errors.append(f"{path}: check {check_id!r}: {error}")
                applies_to = patterns

    changed_command = ""
    if "changed" in item:
        raw_changed = item["changed"]
        if not isinstance(raw_changed, str):
            errors.append(f"{path}: check {check_id!r}: changed must be a string")
        else:
            changed_command = raw_changed.strip()
            if not changed_command:
                errors.append(f"{path}: check {check_id!r}: changed must not be empty")
            elif len(changed_command) > MAX_COMMAND:
                errors.append(
                    f"{path}: check {check_id!r}: changed command is too long "
                    f"(max {MAX_COMMAND} chars)"
                )
            elif "\x00" in changed_command:
                errors.append(f"{path}: check {check_id!r}: changed command contains a NUL byte")

    if command is None or errors:
        # Keep the parsed fields for explain/check-config only when the entry is
        # structurally valid; a broken check must never reach the runner.
        return None, errors

    effective_parser = infer_parser(command, check_id) if parser == "auto" else parser
    kind = (
        normalize_kind(str(item["kind"]))
        if "kind" in item
        else infer_kind(command, check_id, effective_parser)
    )
    if "kind" in item and not kind:
        errors.append(f"{path}: check {check_id!r}: kind must be one of {', '.join(KINDS)}")
        return None, errors

    return (
        CheckCommand(
            id=check_id,
            command=command,
            kind=kind or "other",
            parser=parser,
            timeout=timeout,
            applies_to=applies_to or default_applies_to(effective_parser),
            changed_command=changed_command,
        ),
        errors,
    )
