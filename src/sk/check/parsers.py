"""Parse common test/lint/typecheck output into compact diagnostics.

Every parser is a pure function over captured text. It never runs a tool and
never raises: an output shape the parser does not understand returns an empty
tuple, and the runner labels the raw output as unparsed. That is deliberate —
"no diagnostics" must not be confused with "passed".
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Iterable

SEVERITIES = ("error", "warning", "info")
MAX_MESSAGE = 600


@dataclass(frozen=True)
class Diagnostic:
    file: str
    line: int
    column: int | None
    severity: str
    code: str
    message: str
    source: str = ""
    check: str = ""

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "source": self.source,
            "check": self.check,
        }

    def compact(self) -> str:
        location = self.file or "(no file)"
        if self.line:
            location += f":{self.line}"
            if self.column:
                location += f":{self.column}"
        bits = [location]
        if self.severity:
            bits.append(self.severity)
        if self.code:
            bits.append(self.code)
        bits.append(self.message)
        return "  ".join(bits)


def _norm_path(path: str, root: str = "") -> str:
    value = str(path or "").strip().strip("\"'").replace("\\", "/")
    if value.startswith("file://"):
        value = value[7:]
    while value.startswith("./"):
        value = value[2:]
    if root and os.path.isabs(value):
        try:
            rel = os.path.relpath(value, os.path.abspath(root)).replace("\\", "/")
            if not rel.startswith("../"):
                value = rel
        except Exception:
            pass
    return value


def _clean(message: str) -> str:
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if len(text) > MAX_MESSAGE:
        text = text[: MAX_MESSAGE - 3].rstrip() + "..."
    return text


def _diag(
    *,
    file: str,
    line: int,
    column: int | None,
    severity: str,
    code: str,
    message: str,
    source: str,
    check: str,
    root: str,
) -> Diagnostic:
    return Diagnostic(
        file=_norm_path(file, root),
        line=max(0, int(line or 0)),
        column=int(column) if column else None,
        severity=severity if severity in SEVERITIES else "error",
        code=str(code or "").strip(),
        message=_clean(message),
        source=source,
        check=check,
    )


def _dedupe(items: Iterable[Diagnostic]) -> tuple[Diagnostic, ...]:
    seen: set[tuple[str, int, int | None, str, str, str]] = set()
    out: list[Diagnostic] = []
    for item in items:
        key = (item.file, item.line, item.column, item.severity, item.code, item.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return tuple(out)


# --- pytest -----------------------------------------------------------------

_PYTEST_FAIL = re.compile(r"^\s*(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+(.*))?$")
_PYTEST_SECTION = re.compile(r"^_+\s+(.+?)\s+_+$")
_PYTEST_LOC = re.compile(
    r"^(?P<file>[^\s:][^:]*?\.py):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?P<msg>.*)$"
)


def _pytest_location(nodeid: str) -> tuple[str, str]:
    path, _, test = nodeid.partition("::")
    return path, test


def _pytest_summary_matches(
    section: str, summary: list[tuple[str, str, str]]
) -> tuple[str, str] | None:
    section_key = section.strip()
    for path, test, message in summary:
        if not test:
            continue
        if (
            section_key == test
            or section_key.endswith("::" + test)
            or test.endswith("::" + section_key)
        ):
            return path, message
    return None


def _parse_pytest(text: str, root: str, check: str, exit_code: int) -> tuple[Diagnostic, ...]:
    lines = text.splitlines()
    summary: list[tuple[str, str, str]] = []
    for line in lines:
        match = _PYTEST_FAIL.match(line)
        if not match:
            continue
        kind, nodeid, message = match.groups()
        path, test = _pytest_location(nodeid)
        if not path:
            continue
        summary.append((path, test, message or f"{kind.lower()} {nodeid}"))

    out: list[Diagnostic] = []
    matched_summary: set[int] = set()
    current_section = ""
    current_loc: tuple[str, int, int | None, str] | None = None

    def flush() -> None:
        nonlocal current_loc
        if current_loc is None:
            return
        file, line, column, loc_message = current_loc
        message = loc_message
        path = file
        if current_section:
            found = _pytest_summary_matches(current_section, summary)
            if found is not None:
                summary_path, summary_message = found
                path = summary_path
                message = summary_message
                for index, (spath, stest, _msg) in enumerate(summary):
                    if (
                        spath == summary_path
                        and stest
                        and (current_section.endswith(stest) or stest.endswith(current_section))
                    ):
                        matched_summary.add(index)
                        break
        if not message:
            message = current_section or "test failed"
        out.append(
            _diag(
                file=path,
                line=line,
                column=column,
                severity="error",
                code=current_section.split("::")[-1] if current_section else "",
                message=message,
                source="pytest",
                check=check,
                root=root,
            )
        )
        current_loc = None

    for line in lines:
        section = _PYTEST_SECTION.match(line)
        if section:
            flush()
            current_section = section.group(1).strip()
            continue
        if line.startswith(("=", "-")):
            # Separator lines end a failure body; the summary follows.
            flush()
            current_section = ""
            continue
        loc = _PYTEST_LOC.match(line)
        if loc:
            current_loc = (
                loc.group("file"),
                int(loc.group("line")),
                int(loc.group("col")) if loc.group("col") else None,
                loc.group("msg"),
            )
    flush()

    for index, (path, test, message) in enumerate(summary):
        if index in matched_summary:
            continue
        out.append(
            _diag(
                file=path,
                line=0,
                column=None,
                severity="error",
                code=test,
                message=message,
                source="pytest",
                check=check,
                root=root,
            )
        )

    return _dedupe(out)


# --- ruff -------------------------------------------------------------------

_RUFF = re.compile(
    r"^(?P<file>[^:\n]+?):(?P<line>\d+):(?P<col>\d+):\s*(?:(?P<code>[A-Z]+[0-9]+)\s+)?(?P<msg>.+)$"
)
_RUFF_REFORMAT = re.compile(r"^Would reformat:\s*(?P<file>.+?)\s*$")


def _parse_ruff(text: str, root: str, check: str, exit_code: int) -> tuple[Diagnostic, ...]:
    severity = "error" if exit_code != 0 else "warning"
    out: list[Diagnostic] = []
    for line in text.splitlines():
        match = _RUFF.match(line)
        if match:
            message = match.group("msg").replace("[*]", "").strip()
            out.append(
                _diag(
                    file=match.group("file"),
                    line=int(match.group("line")),
                    column=int(match.group("col")),
                    severity=severity,
                    code=match.group("code") or "",
                    message=message,
                    source="ruff",
                    check=check,
                    root=root,
                )
            )
            continue
        reformat = _RUFF_REFORMAT.match(line)
        if reformat:
            out.append(
                _diag(
                    file=reformat.group("file"),
                    line=0,
                    column=None,
                    severity=severity,
                    code="format",
                    message="would reformat",
                    source="ruff",
                    check=check,
                    root=root,
                )
            )
    return _dedupe(out)


# --- mypy -------------------------------------------------------------------

_MYPY = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+)(?::(?P<col>\d+))?:\s*"
    r"(?P<sev>error|warning|note):\s*(?P<msg>.+)$"
)
_MYPY_CODE = re.compile(r"\s+\[(?P<code>[a-z0-9][a-z0-9-]*)\]\s*$")


def _parse_mypy(text: str, root: str, check: str, exit_code: int) -> tuple[Diagnostic, ...]:
    out: list[Diagnostic] = []
    for line in text.splitlines():
        match = _MYPY.match(line)
        if not match or match.group("sev") == "note":
            continue
        message = match.group("msg").strip()
        code = ""
        code_match = _MYPY_CODE.search(message)
        if code_match:
            code = code_match.group("code")
            message = message[: code_match.start()].rstrip()
        out.append(
            _diag(
                file=match.group("file"),
                line=int(match.group("line")),
                column=int(match.group("col")) if match.group("col") else None,
                severity=match.group("sev"),
                code=code,
                message=message,
                source="mypy",
                check=check,
                root=root,
            )
        )
    return _dedupe(out)


# --- eslint -----------------------------------------------------------------

_ESLINT_LINE = re.compile(
    r"^\s+(?P<line>\d+):(?P<col>\d+)\s+(?P<sev>error|warning)\s+"
    r"(?P<msg>.+?)(?:\s{2,}(?P<code>[\w@/-]+))?\s*$"
)
_ESLINT_COMPACT = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):\s*(?P<msg>.+?)"
    r"(?:\s+\[(?P<sev>Error|Warning)/(?P<code>[\w@/-]+)\])?$"
)
_ESLINT_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue")


def _eslint_json(text: str, root: str, check: str) -> list[Diagnostic]:
    start = text.find("[")
    if start < 0:
        return []
    try:
        data = json.loads(text[start:])
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[Diagnostic] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        file = str(entry.get("filePath") or "")
        messages = entry.get("messages")
        if not isinstance(messages, list):
            continue
        for message in messages:
            if not isinstance(message, dict):
                continue
            severity = "error" if int(message.get("severity") or 0) >= 2 else "warning"
            out.append(
                _diag(
                    file=file,
                    line=int(message.get("line") or 0),
                    column=int(message.get("column") or 0) or None,
                    severity=severity,
                    code=str(message.get("ruleId") or ""),
                    message=str(message.get("message") or "eslint problem"),
                    source="eslint",
                    check=check,
                    root=root,
                )
            )
    return out


def _parse_eslint(text: str, root: str, check: str, exit_code: int) -> tuple[Diagnostic, ...]:
    out = _eslint_json(text, root, check)
    current_file = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not line.startswith((" ", "\t")) and not stripped.startswith("✖"):
            if stripped.endswith(_ESLINT_EXT) or "/" in stripped:
                current_file = stripped
        match = _ESLINT_LINE.match(line)
        if match:
            out.append(
                _diag(
                    file=current_file,
                    line=int(match.group("line")),
                    column=int(match.group("col")),
                    severity=match.group("sev"),
                    code=match.group("code") or "",
                    message=match.group("msg"),
                    source="eslint",
                    check=check,
                    root=root,
                )
            )
            continue
        compact = _ESLINT_COMPACT.match(line)
        if compact and not line.startswith((" ", "\t")):
            severity = (compact.group("sev") or "Error").lower()
            out.append(
                _diag(
                    file=compact.group("file"),
                    line=int(compact.group("line")),
                    column=int(compact.group("col")),
                    severity=severity,
                    code=compact.group("code") or "",
                    message=compact.group("msg"),
                    source="eslint",
                    check=check,
                    root=root,
                )
            )
    return _dedupe(out)


# --- tsc --------------------------------------------------------------------

_TSC_PAREN = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\):\s*"
    r"(?P<sev>error|warning)\s+(?P<code>TS\d+):\s*(?P<msg>.+)$"
)
_TSC_DASH = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+)\s+-\s*"
    r"(?P<sev>error|warning)\s+(?P<code>TS\d+):\s*(?P<msg>.+)$"
)
_TSC_SIMPLE = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\):\s*(?P<sev>error|warning):\s*(?P<msg>.+)$"
)


def _parse_tsc(text: str, root: str, check: str, exit_code: int) -> tuple[Diagnostic, ...]:
    out: list[Diagnostic] = []
    for line in text.splitlines():
        match = _TSC_PAREN.match(line) or _TSC_DASH.match(line)
        if match:
            out.append(
                _diag(
                    file=match.group("file"),
                    line=int(match.group("line")),
                    column=int(match.group("col")),
                    severity=match.group("sev"),
                    code=match.group("code"),
                    message=match.group("msg"),
                    source="tsc",
                    check=check,
                    root=root,
                )
            )
            continue
        simple = _TSC_SIMPLE.match(line)
        if simple:
            out.append(
                _diag(
                    file=simple.group("file"),
                    line=int(simple.group("line")),
                    column=int(simple.group("col")),
                    severity=simple.group("sev"),
                    code="",
                    message=simple.group("msg"),
                    source="tsc",
                    check=check,
                    root=root,
                )
            )
    return _dedupe(out)


# --- go test ----------------------------------------------------------------

_GO_FAIL = re.compile(r"^--- FAIL:\s*(?P<test>\S+)")
_GO_LOC = re.compile(r"^\s*(?P<file>[^\s:]+\.go):(?P<line>\d+):\s*(?P<msg>.+)$")
_GO_PKG_FAIL = re.compile(r"^FAIL\s+(?P<pkg>\S+)(?:\s+\S+)?\s*$")


def _go_json(text: str, root: str, check: str) -> list[Diagnostic]:
    records: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            return []
        if not isinstance(item, dict) or "Action" not in item:
            return []
        records.append(item)

    out: list[Diagnostic] = []
    failed_tests: set[tuple[str, str]] = set()
    failed_packages: set[str] = set()
    tests_with_diag: set[tuple[str, str]] = set()
    for item in records:
        package = str(item.get("Package") or "")
        test = str(item.get("Test") or "")
        output = str(item.get("Output") or "")
        for line in output.splitlines():
            match = _GO_LOC.match(line)
            if match:
                out.append(
                    _diag(
                        file=match.group("file"),
                        line=int(match.group("line")),
                        column=None,
                        severity="error",
                        code=test,
                        message=match.group("msg"),
                        source="go-test",
                        check=check,
                        root=root,
                    )
                )
                if test:
                    tests_with_diag.add((package, test))
        if item.get("Action") == "fail":
            if test:
                failed_tests.add((package, test))
            elif package:
                failed_packages.add(package)

    for package, test in sorted(failed_tests):
        if (package, test) in tests_with_diag:
            continue
        out.append(
            _diag(
                file=package,
                line=0,
                column=None,
                severity="error",
                code=test,
                message=f"test {test} failed",
                source="go-test",
                check=check,
                root=root,
            )
        )
    for package in sorted(failed_packages):
        if any(d.file == _norm_path(package, root) for d in out):
            continue
        out.append(
            _diag(
                file=package,
                line=0,
                column=None,
                severity="error",
                code="",
                message="package tests failed",
                source="go-test",
                check=check,
                root=root,
            )
        )
    return out


def _parse_go_test(text: str, root: str, check: str, exit_code: int) -> tuple[Diagnostic, ...]:
    if any(line.strip().startswith("{") for line in text.splitlines()):
        parsed = _go_json(text, root, check)
        if parsed:
            return _dedupe(parsed)

    out: list[Diagnostic] = []
    current_test = ""
    current_package = ""
    failed_tests: set[str] = set()
    tests_with_diag: set[str] = set()
    for line in text.splitlines():
        fail = _GO_FAIL.match(line)
        if fail:
            current_test = fail.group("test")
            failed_tests.add(current_test)
            continue
        loc = _GO_LOC.match(line)
        if loc:
            out.append(
                _diag(
                    file=loc.group("file"),
                    line=int(loc.group("line")),
                    column=None,
                    severity="error",
                    code=current_test,
                    message=loc.group("msg"),
                    source="go-test",
                    check=check,
                    root=root,
                )
            )
            if current_test:
                tests_with_diag.add(current_test)
            continue
        package = _GO_PKG_FAIL.match(line)
        if package:
            current_package = package.group("pkg")

    for test in sorted(failed_tests - tests_with_diag):
        out.append(
            _diag(
                file=current_package,
                line=0,
                column=None,
                severity="error",
                code=test,
                message=f"test {test} failed",
                source="go-test",
                check=check,
                root=root,
            )
        )
    if not out and current_package:
        out.append(
            _diag(
                file=current_package,
                line=0,
                column=None,
                severity="error",
                code="",
                message="package tests failed",
                source="go-test",
                check=check,
                root=root,
            )
        )
    return _dedupe(out)


_PARSERS = {
    "pytest": _parse_pytest,
    "ruff": _parse_ruff,
    "mypy": _parse_mypy,
    "eslint": _parse_eslint,
    "tsc": _parse_tsc,
    "go-test": _parse_go_test,
}


def parse_output(
    parser: str,
    text: str,
    *,
    root: str = "",
    check: str = "",
    exit_code: int = 1,
) -> tuple[Diagnostic, ...]:
    """Parse captured output. Unknown or raw parsers return an empty tuple."""
    fn = _PARSERS.get(str(parser or "").strip().lower())
    if fn is None:
        return ()
    try:
        return fn(text or "", root, check, int(exit_code))
    except Exception:
        # A parser must never turn a failed check into an exception. The runner
        # will label the output unparsed and preserve the raw fallback.
        return ()
