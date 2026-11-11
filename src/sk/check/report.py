"""Human- and model-readable rendering for check reports."""

from __future__ import annotations

import re

from .runner import CheckReport, CheckResult

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MAX_TEXT_DIAGNOSTICS = 10
MAX_RAW_LINES = 12


def _clean(text: str, limit: int = 600) -> str:
    value = _ANSI.sub("", str(text or "")).replace("\r", "")
    if len(value) > limit:
        value = value[: limit - 3].rstrip() + "..."
    return value


def _status_line(result: CheckResult) -> str:
    detail = []
    if result.exit_code is not None:
        detail.append(f"exit {result.exit_code}")
    if result.timed_out:
        detail.append("timed out")
    if result.truncated:
        detail.append("truncated")
    if result.note:
        detail.append(result.note)
    suffix = f" ({', '.join(detail)})" if detail else ""
    return f"  {result.status.upper():<7} {result.id:<12} {_clean(result.command, 200)}{suffix}"


def format_compact(report: CheckReport) -> str:
    """Plain text: one header, one line per check, compact diagnostics, raw fallback."""
    header = "Checks"
    if report.config_path:
        header += f" · {report.config_path}"
    header += " · " + ("trusted" if report.trusted else "untrusted")
    if report.changed:
        header += f" · changed ({len(report.targeted_files)} files)"
    lines = [header]

    for error in report.config_errors:
        lines.append(f"  CONFIG ERROR: {_clean(error, 1000)}")
    for warning in report.warnings:
        lines.append(f"  warning: {_clean(warning, 1000)}")

    for result in report.results:
        lines.append(_status_line(result))
        if result.diagnostics:
            for diagnostic in result.diagnostics[:MAX_TEXT_DIAGNOSTICS]:
                lines.append(f"    {diagnostic.compact()}")
            remaining = len(result.diagnostics) - MAX_TEXT_DIAGNOSTICS
            if remaining > 0:
                lines.append(f"    ... {remaining} more diagnostics")
        elif result.status in ("fail", "unknown") and result.raw:
            marker = "raw (unparsed)" if result.status == "fail" else "raw"
            lines.append(f"    {marker}:")
            raw_lines = _clean(result.raw, 4000).splitlines()[:MAX_RAW_LINES]
            for raw_line in raw_lines:
                lines.append(f"      {raw_line}")
            if len(_clean(result.raw, 4000).splitlines()) > MAX_RAW_LINES:
                lines.append("      ...")
        elif result.status == "skipped" and result.note:
            continue

    counts = report.counts
    summary = ", ".join(f"{count} {status}" for status, count in counts.items() if count)
    lines.append(f"Summary: {summary}" if summary else "Summary: no checks ran")
    if report.note:
        lines.append(f"Note: {report.note}")
    return "\n".join(lines)
