"""Run configured checks through the normal approval gate and audit ledger.

The runner never calls ``subprocess`` directly. Its default dispatcher is
``sk.agent._gated_dispatch("shell", ...)``, which applies the hard refusals,
PreToolUse hooks, user approval, checkpoints, PostToolUse hooks, and the audit
row exactly as an agent shell call does. A caller that supplies no approval
callback gets an ``unknown`` result, not an unattended run.

Status is a four-way contract:

``pass``     the tool exited 0 and no unknown condition occurred
``fail``     the tool exited non-zero and we can say so
``unknown``  timeout, spawn failure, denial, overflow, or an unparsed failure
``skipped``  ``--changed`` found no files relevant to this check

``unknown`` is never collapsed into ``pass``. A missing pytest binary and a
timed-out mypy run both mean the verification did not happen.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

from ..review.pack import glob_match
from .config import MAX_COMMAND, CheckCommand, CheckPack, load_checks_pack
from .gitutil import CheckGitError, changed_files
from .parsers import Diagnostic, parse_output

STATUSES = ("pass", "fail", "unknown", "skipped")
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2
MAX_DIAGNOSTICS = 50

_EXIT_LINE = re.compile(r"(?m)^\[exit (?P<code>-?\d+)\]\s*$")


class CheckError(RuntimeError):
    """A check set could not be built. The message is safe to show."""


@dataclass(frozen=True)
class CheckResult:
    id: str
    command: str
    kind: str
    parser: str
    status: str
    exit_code: int | None
    duration: float
    diagnostics: tuple[Diagnostic, ...] = ()
    parsed: bool = False
    raw: str = ""
    note: str = ""
    timed_out: bool = False
    truncated: bool = False
    approved: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "command": self.command,
            "kind": self.kind,
            "parser": self.parser,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration": round(self.duration, 3),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "parsed": self.parsed,
            "raw": self.raw,
            "note": self.note,
            "timed_out": self.timed_out,
            "truncated": self.truncated,
            "approved": self.approved,
        }


@dataclass(frozen=True)
class CheckReport:
    results: tuple[CheckResult, ...] = ()
    changed: bool = False
    targeted_files: tuple[str, ...] = ()
    repo_root: str = ""
    config_path: str = ""
    trusted: bool = True
    config_errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    note: str = ""

    @property
    def counts(self) -> dict[str, int]:
        out = {status: 0 for status in STATUSES}
        for result in self.results:
            out[result.status] = out.get(result.status, 0) + 1
        return out

    @property
    def status(self) -> str:
        if self.config_errors:
            return "unknown"
        if any(result.status == "unknown" for result in self.results):
            return "unknown"
        if any(result.status == "fail" for result in self.results):
            return "fail"
        return "pass"

    @property
    def ok(self) -> bool:
        return self.status == "pass"

    @property
    def exit_code(self) -> int:
        if self.config_errors:
            return EXIT_CONFIG
        if self.status in ("fail", "unknown"):
            return EXIT_FAILED
        return EXIT_OK

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        out: list[Diagnostic] = []
        for result in self.results:
            out.extend(result.diagnostics)
        return tuple(out)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "exit_code": self.exit_code,
            "changed": self.changed,
            "targeted_files": list(self.targeted_files),
            "repo_root": self.repo_root,
            "config_path": self.config_path,
            "trusted": self.trusted,
            "config_errors": list(self.config_errors),
            "warnings": list(self.warnings),
            "note": self.note,
            "counts": self.counts,
            "results": [result.to_dict() for result in self.results],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _norm_paths(paths: Iterable[str], root: str) -> tuple[str, ...]:
    out: list[str] = []
    for raw in paths:
        value = str(raw or "").strip().replace("\\", "/")
        while value.startswith("./"):
            value = value[2:]
        if not value:
            continue
        try:
            if Path(value).is_absolute():
                rel = Path(value).resolve().relative_to(Path(root).resolve()).as_posix()
                value = rel
        except Exception:
            pass
        if value not in out:
            out.append(value)
    return tuple(out)


def _substitute_files(template: str, files: Iterable[str]) -> tuple[str, int]:
    """Replace ``{files}`` with shell-quoted paths, capped at the shell limit.

    Returns ``(command, omitted)``. The cap is not cosmetic: the hard refusal
    path rejects commands over 2000 chars before approval, so a huge changed
    file list must be shortened deliberately rather than failing mysteriously.
    """
    if "{files}" not in template:
        return template, 0
    quoted = [shlex.quote(str(path)) for path in files]
    kept = list(quoted)
    while kept:
        candidate = template.replace("{files}", " ".join(kept))
        if len(candidate) <= MAX_COMMAND:
            return candidate, len(quoted) - len(kept)
        kept.pop()
    return template.replace("{files}", "").strip(), len(quoted)


def _missing_tool(raw: str) -> bool:
    lowered = raw.lower()
    return (
        "command not found" in lowered
        or "no such file or directory" in lowered
        or "[errno 2]" in lowered
    )


def _split_shell_result(result: str) -> tuple[int | None, str]:
    match = _EXIT_LINE.search(result or "")
    if match is None:
        return None, (result or "").strip()
    code = int(match.group("code"))
    raw = (result or "")[match.end() :]
    return code, raw.lstrip("\n").strip()


def _unknown_note(result: str, approved: bool) -> str:
    text = (result or "").strip()
    if not approved:
        if "hook" in text.lower():
            return "denied by a PreToolUse hook"
        if "blocked" in text.lower():
            return "refused by the shell hard-block policy"
        return "not approved"
    if "timed out" in text.lower():
        return "timed out"
    if "crashed" in text.lower():
        return "the shell tool failed to start"
    if "command too long" in text.lower():
        return "command exceeds the 2000-char shell limit"
    if text.lower().startswith("error:"):
        return text[:240]
    return "no usable result"


def _run_one(
    check: CheckCommand,
    command: str,
    *,
    root: str,
    approve: Callable[[str, dict], bool] | None,
    dispatch: Callable[..., tuple[str, bool]] | None,
    session: str,
    provider: str,
    host: str,
    max_diagnostics: int,
    note: str = "",
) -> CheckResult:
    parser = check.effective_parser
    base = CheckResult(
        id=check.id,
        command=command,
        kind=check.kind,
        parser=parser,
        status="unknown",
        exit_code=None,
        duration=0.0,
        note=note,
    )
    if approve is None:
        return replace(
            base,
            note="; ".join(x for x in (note, "no approval callback; refusing to run") if x),
        )

    args = {"cmd": command, "timeout": max(1, min(120, int(round(check.timeout))))}
    started = time.monotonic()
    try:
        if dispatch is None:
            from ..agent import _gated_dispatch

            result, approved = _gated_dispatch(
                "shell",
                args,
                approve,
                session=session,
                provider=provider,
                host=host,
            )
        else:
            result, approved = dispatch(
                "shell",
                args,
                approve,
                session=session,
                provider=provider,
                host=host,
            )
    except Exception as e:
        return replace(
            base,
            duration=time.monotonic() - started,
            note="; ".join(x for x in (note, f"runner error: {e}") if x),
        )
    duration = time.monotonic() - started

    exit_code, raw = _split_shell_result(str(result or ""))
    truncated = "... [truncated]" in raw or "output cap reached" in raw
    timed_out = "timed out" in str(result or "").lower()

    if not approved or exit_code is None:
        return replace(
            base,
            duration=duration,
            exit_code=exit_code,
            raw=raw[:6000],
            timed_out=timed_out,
            truncated=truncated,
            approved=bool(approved),
            note="; ".join(x for x in (note, _unknown_note(str(result or ""), approved)) if x),
        )

    if exit_code == 127 and _missing_tool(raw):
        return replace(
            base,
            duration=duration,
            exit_code=exit_code,
            raw=raw[:6000],
            approved=True,
            note="; ".join(x for x in (note, "missing tool or spawn failure") if x),
        )

    if exit_code < 0 and "output cap reached" in raw:
        return replace(
            base,
            duration=duration,
            exit_code=exit_code,
            raw=raw[:6000],
            truncated=True,
            approved=True,
            note="; ".join(x for x in (note, "killed after the output cap") if x),
        )

    diagnostics = parse_output(
        parser,
        raw,
        root=root,
        check=check.id,
        exit_code=exit_code,
    )
    clipped = False
    if len(diagnostics) > max_diagnostics:
        diagnostics = diagnostics[:max_diagnostics]
        clipped = True

    status = "pass" if exit_code == 0 else "fail"
    parsed = bool(diagnostics) or (exit_code == 0 and parser != "raw")
    notes = [note] if note else []
    if status == "fail" and not diagnostics and parser != "raw":
        notes.append(f"unparsed {parser} output")
    if clipped:
        notes.append(f"diagnostics capped at {max_diagnostics}")
    return CheckResult(
        id=check.id,
        command=command,
        kind=check.kind,
        parser=parser,
        status=status,
        exit_code=exit_code,
        duration=duration,
        diagnostics=diagnostics,
        parsed=parsed,
        raw=raw[:6000],
        note="; ".join(notes),
        timed_out=False,
        truncated=truncated,
        approved=True,
    )


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(glob_match(path, pattern) for pattern in patterns)


def run_checks(
    *,
    cwd: str = "",
    changed: bool = False,
    files: Iterable[str] | None = None,
    kinds: Iterable[str] = (),
    trust_project: bool = False,
    approve: Callable[[str, dict], bool] | None = None,
    session: str = "check",
    provider: str = "",
    host: str = "",
    max_diagnostics: int = MAX_DIAGNOSTICS,
    dispatch: Callable[..., tuple[str, bool]] | None = None,
    pack: CheckPack | None = None,
    config_path: str | Path | None = None,
) -> CheckReport:
    """Load and run checks. A missing ``approve`` callback runs nothing.

    ``files`` is for post-edit verification: it narrows the same ``changed``
    logic to an explicit affected-file set. ``changed=True`` fills that set
    from git. ``kinds`` filters to lint/typecheck/test/etc.
    """
    pack = pack or load_checks_pack(
        cwd=cwd,
        trust_project=trust_project,
        path=config_path,
    )
    base = CheckReport(
        repo_root=pack.root,
        config_path=str(pack.path) if pack.path else "",
        trusted=pack.trusted,
        config_errors=pack.errors,
        warnings=pack.warnings,
    )

    if pack.errors:
        return replace(base, note="checks config has errors")
    if pack.present and not pack.trusted:
        return replace(
            base,
            config_errors=(
                "project checks are untrusted; pass --trust-project-checks "
                "or set SIDEKICK_TRUST_REPO=1",
            ),
            note="project checks are untrusted",
        )

    selected = tuple(pack.checks)
    wanted_kinds = {str(kind).strip().lower() for kind in kinds if str(kind).strip()}
    if wanted_kinds:
        selected = tuple(check for check in selected if check.kind in wanted_kinds)
    if not selected:
        return replace(base, note="no checks configured")

    targeted = changed or files is not None
    target_files: tuple[str, ...] = ()
    if files is not None:
        target_files = _norm_paths(files, pack.root)
    elif changed:
        try:
            target_files = changed_files(pack.root)
        except CheckGitError as e:
            return replace(
                base,
                changed=True,
                config_errors=(str(e),),
                note="could not determine changed files",
            )

    if targeted and not target_files:
        skipped_results = tuple(
            CheckResult(
                id=check.id,
                command=check.command,
                kind=check.kind,
                parser=check.effective_parser,
                status="skipped",
                exit_code=None,
                duration=0.0,
                note="no changed files",
            )
            for check in selected
        )
        return replace(
            base,
            results=skipped_results,
            changed=True,
            targeted_files=(),
            note="no changed files",
        )

    limit = max(1, min(500, int(max_diagnostics)))
    results: list[CheckResult] = []
    for check in selected:
        command = check.command
        note = ""
        if targeted:
            matching = tuple(path for path in target_files if _matches(path, check.applies_to))
            if not matching:
                results.append(
                    CheckResult(
                        id=check.id,
                        command=check.command,
                        kind=check.kind,
                        parser=check.effective_parser,
                        status="skipped",
                        exit_code=None,
                        duration=0.0,
                        note="no changed files match this check",
                    )
                )
                continue
            template = check.changed_command or check.command
            command, omitted = _substitute_files(template, matching)
            if omitted:
                note = f"{omitted} of {len(matching)} changed files omitted to fit the shell limit"
        results.append(
            _run_one(
                check,
                command,
                root=pack.root,
                approve=approve,
                dispatch=dispatch,
                session=session,
                provider=provider,
                host=host,
                max_diagnostics=limit,
                note=note,
            )
        )

    return replace(
        base,
        results=tuple(results),
        changed=bool(targeted),
        targeted_files=target_files if targeted else (),
    )
