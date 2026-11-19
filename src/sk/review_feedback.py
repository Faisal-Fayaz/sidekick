"""Local, inspectable review feedback (#514).

A team's "this rule is noise here" knowledge belongs on that team's machine,
not in a shared pack or a prompt. Feedback is a global-only JSONL log; repeated
rejects suppress a rule (globally or for one file), repeated accepts pin it as
trusted. Nothing here edits a review pack or a project file.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

FEEDBACK_FILE = "review-feedback.jsonl"
THRESHOLD = 2
RULE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,79}$")
VERDICTS = ("accept", "reject")


@dataclass(frozen=True)
class Entry:
    rule_id: str
    verdict: str
    file: str = ""
    ts: float = 0.0

    @property
    def key(self) -> tuple[str, str]:
        return (self.rule_id, self.file)


def _path() -> Path:
    from .config import CONFIG_DIR

    return Path(CONFIG_DIR) / FEEDBACK_FILE


def entries() -> list[Entry]:
    out: list[Entry] = []
    try:
        for line in _path().read_text(errors="replace").splitlines():
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            rule = str(obj.get("rule_id") or "")
            verdict = str(obj.get("verdict") or "")
            if RULE_RE.fullmatch(rule) and verdict in VERDICTS:
                out.append(
                    Entry(
                        rule, verdict, str(obj.get("file") or "")[:500], float(obj.get("ts") or 0)
                    )
                )
    except Exception:
        pass
    return out


def record(rule_id: str, verdict: str, file: str = "") -> str:
    rule = str(rule_id or "").strip()
    if not RULE_RE.fullmatch(rule):
        return f"invalid rule id {rule!r} (letters, digits, . _ / - only)"
    if str(verdict).strip().lower() not in VERDICTS:
        return "verdict must be accept or reject"
    path = _path()
    try:
        from .fsperm import PRIVATE_FILE, ensure_private_dir, tighten

        ensure_private_dir(path.parent)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "rule_id": rule,
                        "verdict": str(verdict).strip().lower(),
                        "file": str(file or "").strip()[:500],
                        "ts": time.time(),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        tighten(path, want=PRIVATE_FILE)
    except Exception as e:  # noqa: BLE001 - a feedback write failure is a report
        return f"could not save feedback: {e}"
    return f"Recorded {verdict} for {rule}" + (f" on {file}" if file else "")


def forget(rule_id: str = "", file: str = "") -> str:
    rule = str(rule_id or "").strip()
    wanted_file = str(file or "").strip()

    def keep(entry: Entry) -> bool:
        if not rule:
            return False
        if wanted_file:
            return not (entry.rule_id == rule and entry.file == wanted_file)
        return entry.rule_id != rule

    kept = [e for e in entries() if keep(e)]
    try:
        _path().write_text("".join(json.dumps(e.__dict__, sort_keys=True) + "\n" for e in kept))
    except Exception as e:  # noqa: BLE001 - a feedback delete failure is a report
        return f"could not forget feedback: {e}"
    return f"Forgot feedback for {rule or 'all rules'}"


@dataclass(frozen=True)
class Learned:
    suppressed: frozenset[str]
    suppressed_files: frozenset[tuple[str, str]]
    trusted: frozenset[str]


def learned() -> Learned:
    all_entries = entries()
    global_rejects = Counter(e.rule_id for e in all_entries if not e.file and e.verdict == "reject")
    file_rejects = Counter(e.key for e in all_entries if e.file and e.verdict == "reject")
    accepts = Counter(e.rule_id for e in all_entries if e.verdict == "accept")
    trusted = frozenset(rule for rule, n in accepts.items() if n >= THRESHOLD)
    return Learned(
        suppressed=frozenset(rule for rule, n in global_rejects.items() if n >= THRESHOLD)
        - trusted,
        suppressed_files=frozenset(
            key for key, n in file_rejects.items() if n >= THRESHOLD and key[0] not in trusted
        ),
        trusted=trusted,
    )


def apply(findings: list) -> tuple[list, int]:
    """Return (kept findings, number suppressed). Trust wins over suppression."""
    import dataclasses

    rules = learned()
    kept = []
    suppressed = 0
    for finding in findings:
        rule = str(getattr(finding, "rule_id", "") or "")
        file = str(getattr(finding, "file", "") or "")
        if rule in rules.trusted:
            if getattr(finding, "confidence", "") != "high":
                finding = dataclasses.replace(finding, confidence="high")
            kept.append(finding)
            continue
        if rule in rules.suppressed or (rule, file) in rules.suppressed_files:
            suppressed += 1
            continue
        kept.append(finding)
    return kept, suppressed


def summary() -> list[str]:
    rules = learned()
    lines = []
    for rule in sorted(rules.trusted):
        lines.append(f"- {rule}: trusted (accepted at least {THRESHOLD} times)")
    for rule in sorted(rules.suppressed):
        lines.append(f"- {rule}: suppressed globally")
    for rule, file in sorted(rules.suppressed_files):
        lines.append(f"- {rule} on {file}: suppressed")
    return lines
