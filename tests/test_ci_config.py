"""The hand-duplicated config lists must agree (#270, #319).

Three files carry the same mypy path list: a comment in pyproject.toml, the
pre-commit `entry`, and the CI `run`. It has drifted before, and a mismatch means
pre-commit and CI disagree about what is type-checked. Rather than collapse the
three copies, assert they agree — collapsing would mean restructuring the
pre-commit hook and the CI matrix, which is not worth it here.

Same for the ruff version: pre-commit and CI once disagreed (0.8.4 vs whatever
formatted the tree), and the older one wanted to reformat four files.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _mypy_lists() -> tuple[list[str], list[str], list[str]]:
    ci = [
        ln
        for ln in (ROOT / ".github/workflows/ci.yml").read_text().splitlines()
        if "uvx mypy" in ln
    ]
    pc = [
        ln for ln in (ROOT / ".pre-commit-config.yaml").read_text().splitlines() if "uvx mypy" in ln
    ]
    py = (ROOT / "pyproject.toml").read_text()
    assert ci and pc, "mypy gate line not found"
    a = re.sub(r"^\s*run: uvx mypy@[\d.]+ ", "", ci[0]).split()
    b = re.sub(r"^\s*entry: uvx mypy ", "", pc[0]).split()
    # pyproject wraps the same list across `#   ` continuation comments
    c: list[str] = []
    in_block = False
    for line in py.splitlines():
        if line.startswith("# CI gate: mypy "):
            in_block = True
            c += line[len("# CI gate: mypy ") :].split()
            continue
        if in_block:
            if not line.startswith("#   "):
                break
            c += line[len("#   ") :].split()
    return a, b, c


def test_mypy_gate_lists_agree():
    a, b, c = _mypy_lists()
    assert a == b, (
        f"CI and pre-commit differ:\n  ci only: {set(a) - set(b)}\n  pc only: {set(b) - set(a)}"
    )
    assert set(c) == set(a), (
        f"pyproject comment differs:\n  doc only: {set(c) - set(a)}\n  real only: {set(a) - set(c)}"
    )


def test_ruff_versions_agree():
    pc = (ROOT / ".pre-commit-config.yaml").read_text()
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    m = re.search(r"rev: v([\d.]+)", pc)
    assert m, "ruff rev not found in pre-commit"
    pc_v = m.group(1)
    ci_v = re.search(r"uvx ruff@([\d.]+)", ci)
    assert ci_v, "ruff version not pinned in CI"
    assert pc_v == ci_v.group(1), (
        f"pre-commit pins ruff {pc_v}, CI pins {ci_v.group(1)}; "
        "the tree can only be formatted to one of them"
    )


def test_mypy_gate_covers_the_modules_that_ship_in_the_request_path():
    """trust.py and agent.py must both be gated."""
    a, _b, _c = _mypy_lists()
    for required in ("src/sk/agent.py", "src/sk/trust.py", "src/sk/tools"):
        assert required in a, f"{required} is not type-checked"


def test_badge_gate_does_not_grep_the_readme():
    """The badge gate failed any PR that added a test, and false-passed on
    collection errors. It must verify collection succeeds, nothing more."""
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert 'grep -q "tests-' not in ci, "badge gate greps the README again"
    assert "--collect-only" in ci, "collection is no longer verified"
    readme = (ROOT / "README.md").read_text()
    assert "tests-9" not in readme and "tests-1" not in readme, "hand-maintained count is back"
