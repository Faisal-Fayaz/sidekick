"""The documented platform decision must match CI and stay explicit (#528)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
DOC = ROOT / "docs" / "platforms.md"


def _ci_matrix() -> tuple[set[str], set[str]]:
    text = CI.read_text()
    os_match = re.search(r"os:\s*\[([^\]]+)\]", text)
    py_match = re.search(r"python-version:\s*\[([^\]]+)\]", text)
    assert os_match and py_match, "the CI test matrix is no longer parseable"
    operating_systems = {item.strip() for item in os_match.group(1).split(",")}
    pythons = {item.strip().strip("\"'") for item in py_match.group(1).split(",")}
    return operating_systems, pythons


def _doc_matrix() -> tuple[set[str], set[str]]:
    text = DOC.read_text()
    operating_systems: set[str] = set()
    pythons: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or "CI matrix leg" not in cells[2]:
            continue
        os_name = cells[0].split(" (")[0]
        operating_systems.add(os_name)
        pythons |= {v.strip() for v in cells[1].split(",")}
    return operating_systems, pythons


def test_documented_matrix_matches_ci():
    ci_os, ci_py = _ci_matrix()
    doc_os, doc_py = _doc_matrix()
    assert doc_os == ci_os, f"docs say {sorted(doc_os)}, CI runs {sorted(ci_os)}"
    assert doc_py == ci_py, f"docs say {sorted(doc_py)}, CI runs {sorted(ci_py)}"


def test_windows_is_an_explicit_non_goal():
    text = DOC.read_text()
    assert "Windows: a non-goal" in text
    for blocker in (
        "sh -c",
        "chmod 0600",
        "Unix domain",
        "bubblewrap",
        "Seatbelt",
        "Windows CI",
    ):
        # The CI blocker is phrased as "no Windows matrix leg"; accept either.
        if blocker == "Windows CI":
            assert "Windows job" in text or "Windows matrix" in text
        else:
            assert blocker in text, f"missing Windows blocker: {blocker}"


def test_readme_links_the_decision():
    assert "docs/platforms.md" in (ROOT / "README.md").read_text()


def test_the_decision_has_a_date():
    assert "decided 2026-11-20" in DOC.read_text()
