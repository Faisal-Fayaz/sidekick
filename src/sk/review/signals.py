"""Deterministic worth-it signals derived from the diff.

Every signal is a fact about the diff, not a model guess. The assessment prompt
gets these signals and may cite them; it may not invent new ones.
"""

from __future__ import annotations

import re

from .diff import DiffModel
from .intent import Intent

_TEST_PATH = re.compile(
    r"(^|/)(tests?|specs?|__tests__)/|(^|/)(test_.*\.py|.*_test\.py|.*\.test\.\w+|.*\.spec\.\w+)$"
)
_DEPS = re.compile(
    r"(^|/)(pyproject\.toml|setup\.py|setup\.cfg|requirements[^/]*\.txt|package\.json|"
    r"go\.mod|Cargo\.toml|Gemfile|pom\.xml|build\.gradle)$"
)
_DOCS = re.compile(r"(^|/)(README|CHANGELOG|CONTRIBUTING|docs?/|.*\.md$)", re.IGNORECASE)
_MIGRATION = re.compile(r"(^|/)(migrations?|alembic|schema)/|migration", re.IGNORECASE)
_SECURITY = re.compile(
    r"(^|/)(auth|security|crypto|permissions?|secrets?|tokens?|oauth|session)/|"
    r"(^|/)(auth|security|crypto|permissions?|secrets?|tokens?)\.\w+$",
    re.IGNORECASE,
)
_CONFIG = re.compile(
    r"(^|/)(\.github/|Dockerfile|docker-compose|Makefile|\.sidekick\.toml|\.env\.example)"
)


def compute_signals(diff: DiffModel, intent: Intent) -> list[str]:
    files = list(diff.files)
    paths = [f.path for f in files]
    additions = sum(f.additions for f in files)
    deletions = sum(f.deletions for f in files)
    signals: list[str] = []

    if not intent.available:
        signals.append("No stated intent was found in the PR, commit message, or --intent.")
    signals.append(
        f"Diff touches {len(files)} file(s) and {diff.changed_lines} changed line(s) "
        f"(+{additions}/-{deletions})."
    )
    if diff.truncated:
        signals.append("The diff exceeded the review budget and was truncated.")
    if len(files) > 20 or diff.changed_lines > 500:
        signals.append("Large diff: review fatigue and missed interactions are more likely.")

    test_files = [p for p in paths if _TEST_PATH.search(p)]
    code_files = [f for f in files if not _TEST_PATH.search(f.path) and not f.skipped]
    if test_files:
        signals.append("Tests were changed: " + ", ".join(test_files[:4]) + ".")
    elif code_files:
        signals.append("No test files were changed alongside code changes.")

    dep_files = [p for p in paths if _DEPS.search(p)]
    if dep_files:
        deps = _dependency_names(diff)
        suffix = f" ({', '.join(deps[:6])})" if deps else ""
        signals.append("Dependency/build files changed: " + ", ".join(dep_files[:4]) + suffix + ".")

    migrations = [p for p in paths if _MIGRATION.search(p)]
    if migrations:
        signals.append(
            "Database/schema migration paths changed: " + ", ".join(migrations[:4]) + "."
        )

    security = [p for p in paths if _SECURITY.search(p)]
    if security:
        signals.append("Security-sensitive paths changed: " + ", ".join(security[:4]) + ".")

    docs = [p for p in paths if _DOCS.search(p)]
    if docs:
        signals.append("Docs changed: " + ", ".join(docs[:4]) + ".")

    config = [p for p in paths if _CONFIG.search(p)]
    if config:
        signals.append("Build/CI/config changed: " + ", ".join(config[:4]) + ".")

    if diff.skipped:
        signals.append(
            f"{len(diff.skipped)} file(s) skipped by the diff budget "
            "(binary/generated/lockfile or file cap)."
        )
    return signals


def _dependency_names(diff: DiffModel) -> list[str]:
    names: list[str] = []
    for line in diff.raw.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:].strip()
        if not body or body.startswith("#"):
            continue
        m = re.match(r'^["\']?([A-Za-z0-9_.-]{2,40})["\']?\s*[=<>~!]', body)
        if m:
            names.append(m.group(1))
        elif re.match(r"^[A-Za-z0-9_.-]{2,40}$", body):
            names.append(body)
    return list(dict.fromkeys(names))
