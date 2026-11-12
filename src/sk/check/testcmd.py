"""Detect the project's test command (#452).

The command is *detected*, never invented. It comes from a file in the
repository — a Makefile target, a ``package.json`` script, a ``Cargo.toml``, a
``go.mod``, or a Python project — so a model cannot talk the runner into
executing something the project never asked for.

The project's own declaration wins: if `.sidekick/checks.toml` declares a
check of kind ``test``, that is the command, and nothing here is consulted.
"""

from __future__ import annotations

import json
from pathlib import Path

# Markers that mean "this is a Python project that uses pytest". The first
# three are configurations; the last is a convention.
_PYTHON_MARKERS = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")


def _makefile_test(base: Path) -> str:
    """`make test`, when the Makefile declares that target."""
    for name in ("Makefile", "makefile", "GNUmakefile"):
        path = base / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except Exception:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("test:") and not stripped.startswith(".PHONY"):
                return "make test"
    return ""


def _npm_test(base: Path) -> str:
    """`npm test`, when package.json declares a test script."""
    path = base / "package.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(errors="replace"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    scripts = data.get("scripts")
    if isinstance(scripts, dict) and "test" in scripts:
        return "npm test --silent"
    return ""


def detect_test_command(root: str | Path = ".") -> str:
    """The test command for this project, or "" when nothing is obvious.

    Detection order is deliberate: the project's own declaration first
    (Makefile, package.json), then the ecosystem marker for the language
    (Cargo.toml, go.mod), then Python.
    """
    base = Path(root or ".").resolve()
    try:
        if not base.is_dir():
            return ""
    except Exception:
        return ""

    for probe in (_makefile_test, _npm_test):
        try:
            found = probe(base)
        except Exception:
            found = ""
        if found:
            return found

    if (base / "Cargo.toml").is_file():
        return "cargo test"
    if (base / "go.mod").is_file():
        return "go test ./..."
    for marker in _PYTHON_MARKERS:
        if (base / marker).is_file():
            return "pytest -q"
    try:
        if (base / "tests").is_dir() or any(base.glob("test_*.py")):
            return "pytest -q"
    except Exception:
        pass
    return ""
