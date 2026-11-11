"""Post-edit verification: fast syntax checks plus configured project checks.

The old verification step compiled edited Python files only. This module keeps
that as the first, fastest layer and adds the project's own lint/typecheck
checks for the affected files. Every command goes through
:func:`sk.check.runner.run_checks`, which routes through
``sk.agent._gated_dispatch``: hard refusals, hooks, approval, checkpoints, and
the audit ledger all still apply.

A failing configured check is a failed verification, not a generic tool error.
Missing tools, timeouts, denials, and untrusted configuration are ``unknown``
and never ``pass``.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .check.config import CheckCommand, CheckPack, load_checks_pack
from .check.gitutil import project_root
from .check.parsers import Diagnostic
from .check.runner import CheckResult, run_checks

STATUSES = ("pass", "fail", "unknown", "skipped")
MAX_SYNTAX_FILES = 20
SYNTAX_TIMEOUT = 60.0
MAX_TEXT_DIAGNOSTICS = 8
MAX_RAW_LINES = 8
MAX_COMPACT_LINES = 60
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass(frozen=True)
class VerificationResult:
    """Combined syntax + configured-check verdict for one edit set."""

    status: str = "skipped"
    results: tuple[CheckResult, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    full: bool = False
    root: str = ""
    files: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        out = {status: 0 for status in STATUSES}
        for result in self.results:
            out[result.status] = out.get(result.status, 0) + 1
        return out

    @property
    def repairable(self) -> bool:
        """True when the model could plausibly fix the failure."""
        return self.status == "fail" or any(result.status == "fail" for result in self.results)

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        out: list[Diagnostic] = []
        for result in self.results:
            out.extend(result.diagnostics)
        return tuple(out)

    def to_dict(self) -> dict:
        return {
            "ok": self.status == "pass",
            "status": self.status,
            "full": self.full,
            "root": self.root,
            "files": list(self.files),
            "counts": self.counts,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "results": [result.to_dict() for result in self.results],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    def compact(self) -> str:
        lines = [f"verification: {self.status}"]
        for error in self.errors:
            lines.append(f"  CONFIG ERROR: {_clean(error, 800)}")
        for warning in self.warnings:
            lines.append(f"  warning: {_clean(warning, 800)}")
        for result in self.results:
            detail = []
            if result.exit_code is not None:
                detail.append(f"exit {result.exit_code}")
            if result.note:
                detail.append(result.note)
            suffix = f" ({', '.join(detail)})" if detail else ""
            lines.append(
                f"  {result.status.upper():<7} {result.id:<12} "
                f"{_clean(result.command, 200)}{suffix}"
            )
            if result.diagnostics:
                for diagnostic in result.diagnostics[:MAX_TEXT_DIAGNOSTICS]:
                    lines.append(f"    {diagnostic.compact()}")
                remaining = len(result.diagnostics) - MAX_TEXT_DIAGNOSTICS
                if remaining > 0:
                    lines.append(f"    ... {remaining} more diagnostics")
            elif result.status in ("fail", "unknown") and result.raw:
                marker = "raw (unparsed)" if result.status == "fail" else "raw"
                raw = _clean(result.raw, 3000)
                lines.append(f"    {marker}:")
                for raw_line in raw.splitlines()[:MAX_RAW_LINES]:
                    lines.append(f"      {raw_line}")
                if len(raw.splitlines()) > MAX_RAW_LINES:
                    lines.append("      ...")
        if len(lines) > MAX_COMPACT_LINES:
            remaining = len(lines) - MAX_COMPACT_LINES
            lines = lines[:MAX_COMPACT_LINES] + [f"  ... ({remaining} more lines)"]
        return "\n".join(lines)


def _clean(text: str, limit: int = 400) -> str:
    value = _ANSI.sub("", str(text or "")).replace("\r", "")
    if len(value) > limit:
        value = value[: limit - 3].rstrip() + "..."
    return value


def _shell_files(files: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(path)) for path in files)


def _python() -> str:
    # The verifier is running inside Python, so this interpreter is guaranteed
    # to exist even when `python3` is not on the shell PATH.
    return shlex.quote(sys.executable or "python3")


def _python_syntax(files: list[str]) -> str:
    code = "import sys,tokenize;[compile(tokenize.open(f).read(),f,'exec') for f in sys.argv[1:]]"
    return f"{_python()} -c {shlex.quote(code)} {_shell_files(files)}"


def _json_syntax(files: list[str]) -> str:
    code = "import json,sys;[json.load(open(f,encoding='utf-8')) for f in sys.argv[1:]]"
    return f"{_python()} -c {shlex.quote(code)} {_shell_files(files)}"


def _toml_syntax(files: list[str]) -> str:
    code = "import sys,tomllib;[tomllib.load(open(f,'rb')) for f in sys.argv[1:]]"
    return f"{_python()} -c {shlex.quote(code)} {_shell_files(files)}"


def _bash_syntax(files: list[str]) -> str:
    return f'for f in {_shell_files(files)}; do bash -n "$f" || exit $?; done'


def _node_syntax(files: list[str]) -> str:
    return f'for f in {_shell_files(files)}; do node --check "$f" || exit $?; done'


_EXTENSIONS: dict[str, tuple[str, Callable[[list[str]], str]]] = {
    ".py": ("syntax-python", _python_syntax),
    ".pyi": ("syntax-python", _python_syntax),
    ".json": ("syntax-json", _json_syntax),
    ".toml": ("syntax-toml", _toml_syntax),
    ".sh": ("syntax-shell", _bash_syntax),
    ".bash": ("syntax-shell", _bash_syntax),
    ".js": ("syntax-js", _node_syntax),
    ".mjs": ("syntax-js", _node_syntax),
    ".cjs": ("syntax-js", _node_syntax),
}


def _syntax_checks(files: Iterable[str], root: str) -> tuple[CheckCommand, ...]:
    """Build one fast syntax command per language, skipping missing files.

    ``files`` are repo-relative to ``root``. A file that is not present (a
    deleted diff entry, or a PR checkout that does not exist locally) is not a
    syntax failure; configured checks still run on the affected set.
    """
    groups: dict[str, tuple[str, Callable[[list[str]], str], list[str]]] = {}
    seen = 0
    for raw in files:
        if seen >= MAX_SYNTAX_FILES:
            break
        path = Path(raw)
        if not path.is_absolute():
            path = Path(root) / path
        if not path.is_file():
            continue
        key = _EXTENSIONS.get(path.suffix.lower())
        if key is None:
            continue
        check_id, builder = key
        entry = groups.setdefault(check_id, (check_id, builder, []))
        entry[2].append(raw)
        seen += 1

    out: list[CheckCommand] = []
    for check_id, builder, paths in groups.values():
        out.append(
            CheckCommand(
                id=check_id,
                command=builder(paths),
                kind="syntax",
                parser="raw",
                timeout=SYNTAX_TIMEOUT,
                applies_to=("**/*",),
            )
        )
    return tuple(out)


def _relative_paths(files: Iterable[str], root: str, cwd: str) -> tuple[str, ...]:
    base = Path(os.path.abspath(cwd or os.getcwd()))
    root_path = Path(os.path.abspath(root or base))
    out: list[str] = []
    for raw in files:
        value = str(raw or "").strip()
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = base / path
        try:
            relative = path.resolve(strict=False).relative_to(root_path.resolve())
            value = relative.as_posix()
        except Exception:
            value = str(path.resolve(strict=False))
        if value not in out:
            out.append(value)
    return tuple(out)


def _status(results: tuple[CheckResult, ...], errors: Iterable[str]) -> str:
    if tuple(errors):
        return "unknown"
    if any(result.status == "unknown" for result in results):
        return "unknown"
    if any(result.status == "fail" for result in results):
        return "fail"
    return "pass" if results else "skipped"


def verify_files(
    files: Iterable[str],
    *,
    cwd: str = "",
    trust_project: bool = False,
    full: bool = False,
    approve: Callable[[str, dict], bool] | None = None,
    session: str = "verify",
    provider: str = "",
    host: str = "",
    on_command: Callable[[str], None] | None = None,
    dispatch: Callable[..., tuple[str, bool]] | None = None,
) -> VerificationResult:
    """Verify edited ``files`` without ever raising.

    ``full`` runs every configured check; the default runs only the
    ``lint``/``typecheck`` kinds against the affected files. The project pack
    is untrusted unless ``trust_project`` or ``SIDEKICK_TRUST_REPO=1`` is set.
    A missing approval callback makes each check ``unknown`` (the runner's
    contract); the agent passes an explicit callback for its auto-approve mode.
    """
    root = project_root(cwd or os.getcwd())
    try:
        normalized = _relative_paths(files, root, cwd or os.getcwd())
        results: list[CheckResult] = []
        errors: list[str] = []
        warnings: list[str] = []

        syntax = _syntax_checks(normalized, root)
        if syntax:
            report = run_checks(
                pack=CheckPack(root=root, present=False, trusted=True, checks=syntax),
                approve=approve,
                session=session,
                provider=provider,
                host=host,
                on_command=on_command,
                dispatch=dispatch,
            )
            results.extend(result for result in report.results if result.status != "skipped")
            errors.extend(report.config_errors)

        pack = load_checks_pack(cwd=root, trust_project=trust_project)
        warnings.extend(pack.warnings)
        if pack.errors:
            errors.extend(pack.errors)
        elif pack.present and not pack.trusted and pack.declared:
            # A checks file exists, but its commands were not loaded. Reporting
            # `pass` here would claim the configured verification happened when
            # it did not; the honest verdict is `unknown`.
            errors.append(
                f"{pack.path}: project checks are untrusted; pass "
                "--trust-project-checks or set SIDEKICK_TRUST_REPO=1"
            )
        elif pack.checks:
            report = run_checks(
                pack=pack,
                files=None if full else normalized,
                kinds=() if full else ("lint", "typecheck"),
                approve=approve,
                session=session,
                provider=provider,
                host=host,
                on_command=on_command,
                dispatch=dispatch,
            )
            results.extend(result for result in report.results if result.status != "skipped")
            errors.extend(report.config_errors)

        return VerificationResult(
            status=_status(tuple(results), errors),
            results=tuple(results),
            errors=tuple(dict.fromkeys(errors)),
            warnings=tuple(dict.fromkeys(warnings)),
            full=full,
            root=root,
            files=normalized,
        )
    except Exception as e:  # noqa: BLE001 - verification must never break a turn
        return VerificationResult(
            status="unknown",
            errors=(f"verification failed: {type(e).__name__}: {e}",),
            full=full,
            root=root,
        )
