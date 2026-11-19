"""Local enterprise surface: identity, a global tool policy and an offline report (#513).

Nothing here phones home. Identity comes from the operator's environment (an
upstream IdP can set it); the tool policy comes only from the user's global
config; the report is assembled from the local audit ledger and rendered as a
self-contained file with no remote resources.
"""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

POLICY_FILE = "policy.toml"

_SECRET_PATTERNS = (
    re.compile(r"sk-or-[A-Za-z0-9_-]+"),
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"(api[_-]?key|token|secret|password)\s*[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"https?://\S+", re.IGNORECASE),
)


def identity() -> tuple[str, str]:
    """(actor, team) supplied from the environment; empty when not configured."""
    return (
        os.getenv("SIDEKICK_ACTOR", "").strip()[:120],
        os.getenv("SIDEKICK_TEAM", "").strip()[:120],
    )


def _global_policy() -> dict:
    from .config import CONFIG_PATH

    try:
        import tomllib

        with open(CONFIG_PATH, "rb") as handle:
            data = tomllib.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def denied_tools() -> tuple[str, ...]:
    """Tools denied by the operator's global policy. A repo cannot set this."""
    raw = _global_policy().get("deny_tools", ())
    if isinstance(raw, str):
        raw = (raw,)
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(sorted({str(item).strip().lower() for item in raw if str(item).strip()}))


def check_policy(name: str) -> str:
    """A refusal reason when `name` is denied, else ""."""
    if str(name).strip().lower() in denied_tools():
        return f"tool '{name}' is denied by ~/.sidekick/{POLICY_FILE} deny_tools"
    return ""


def redact(text: object) -> str:
    out = str(text if text is not None else "")
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("<redacted>", out)
    return out[:300]


def _counts(rows: list[dict]) -> dict[str, int]:
    return {
        "total": len(rows),
        "allowed": sum(1 for r in rows if r.get("approved")),
        "denied": sum(1 for r in rows if not r.get("approved")),
        "failed": sum(1 for r in rows if r.get("approved") and not r.get("ok", True)),
        "taint": sum(1 for r in rows if "taint=" in str(r.get("target", ""))),
        "egress": sum(1 for r in rows if str(r.get("provider", "")) not in ("", "local")),
    }


def _by(rows: list[dict], key: str) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in rows:
        name = str(row.get(key) or "(none)")
        counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def render_report(rows: list[dict], *, format: str = "md", session: str = "") -> str:
    """A self-contained Markdown or HTML report. Never raises on odd rows."""
    # Skip malformed rows rather than counting them as runs; the ledger can be
    # hand-edited or from an older schema.
    clean = [r for r in rows if isinstance(r, dict) and "tool" in r]
    if session:
        clean = [r for r in clean if str(r.get("session", "")) == session]
    counts = _counts(clean)
    scope = f"session `{html.escape(session)}`" if session else "all sessions"
    summary = (
        f"{counts['total']} runs · {counts['allowed']} allowed · {counts['denied']} denied · "
        f"{counts['failed']} failed · {counts['taint']} taint decisions · {counts['egress']} egress"
    )

    if format.strip().lower().startswith("html"):
        parts = [
            "<!doctype html><meta charset='utf-8'>",
            "<title>Sidekick audit report</title>",
            "<style>body{font:14px system-ui;margin:2rem;max-width:70rem}"
            "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:.3rem}"
            ".deny{color:#a00}</style>",
            "<h1>Sidekick audit report</h1>",
            f"<p>{html.escape(scope)} — {html.escape(summary)}</p>",
        ]
        for key, label in (("session", "Sessions"), ("actor", "Actors")):
            parts.append(f"<h2>{label}</h2><table><tr><th>name</th><th>runs</th></tr>")
            parts += [f"<tr><td>{html.escape(n)}</td><td>{c}</td></tr>" for n, c in _by(clean, key)]
            parts.append("</table>")
        parts.append("<h2>Runs</h2><table><tr><th>tool</th><th>target</th><th>decision</th></tr>")
        for row in reversed(clean[-500:]):
            decision = "<span class='deny'>DENIED</span>" if not row.get("approved") else "allowed"
            parts.append(
                "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    html.escape(str(row.get("tool", ""))),
                    html.escape(redact(row.get("target", ""))),
                    decision,
                )
            )
        parts.append("</table>")
        return "\n".join(parts)

    lines = ["# Sidekick audit report", "", f"Scope: {scope}", "", summary, "", "## Sessions"]
    lines += [f"- {n}: {c}" for n, c in _by(clean, "session")] or ["- (none)"]
    lines += ["", "## Actors"]
    lines += [f"- {n}: {c}" for n, c in _by(clean, "actor")] or ["- (none)"]
    lines += ["", "## Runs", "", "| tool | target | decision |", "|---|---|---|"]
    for row in reversed(clean[-500:]):
        decision = "DENIED" if not row.get("approved") else "allowed"
        target = redact(row.get("target", "")).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {row.get('tool', '')} | {target} | {decision} |")
    return "\n".join(lines) + "\n"


def write_report(rows: list[dict], *, format: str = "md", session: str = "", out: str = "") -> Path:
    text = render_report(rows, format=format, session=session)
    if not out:
        from .config import CONFIG_DIR

        suffix = "html" if format.strip().lower().startswith("html") else "md"
        target = Path(CONFIG_DIR) / f"audit-report.{suffix}"
    else:
        target = Path(out).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
