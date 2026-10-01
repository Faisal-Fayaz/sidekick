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


# --- required status checks on main -----------------------------------------

# Branch protection is a GitHub setting, so it is invisible to every other check
# in this file. The failure it causes is also unusually nasty: a required
# context that no job ever reports leaves main unable to accept a push, and the
# reason is not visible anywhere in the repository. A merge of #353 produced no
# CI run at all and nothing complained — this is the part that was fixable in
# the tree.


def _ci_jobs_block() -> str:
    """Everything under `jobs:` — the `on:` block has 2-space keys too, so a
    naive scan finds `push:` and `schedule:` as if they were jobs."""
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    return ci[ci.index("\njobs:\n") :]


def _ci_job_names() -> set[str]:
    """Job names in ci.yml, expanding the test matrix into check names."""
    block = _ci_jobs_block()
    jobs = set(re.findall(r"^  ([a-z][a-z0-9-]*):$", block, re.M))
    versions = re.search(r'python-version: \["([\d.]+)", "([\d.]+)", "([\d.]+)"\]', block)
    oses = re.search(r"os: \[([^\]]+)\]", block)
    assert versions and oses, "could not read the test matrix out of ci.yml"
    matrix = {
        f"test ({o.strip()}, {v})" for o in oses.group(1).split(",") for v in versions.groups()
    }
    return (jobs - {"test"}) | matrix


def _required_manifest() -> dict:
    import json

    return json.loads((ROOT / ".github/required-checks.json").read_text())


def test_required_checks_manifest_matches_ci_jobs():
    """Every job is either required or explicitly excluded, and vice versa."""
    manifest = _required_manifest()
    jobs = _ci_job_names()
    required = set(manifest["contexts"])
    excluded = set(manifest["excluded"])

    unknown = required - jobs
    assert not unknown, f"required context(s) no ci.yml job produces: {sorted(unknown)}"

    unaccounted = jobs - required - excluded
    assert not unaccounted, (
        f"ci.yml job(s) neither required nor listed as excluded: {sorted(unaccounted)}. "
        "Add to .github/required-checks.json — a new job is not automatically gated."
    )


def test_required_checks_contexts_are_unique():
    manifest = _required_manifest()
    contexts = manifest["contexts"]
    assert len(contexts) == len(set(contexts)), "duplicate required context"


def test_release_guard_is_excluded_because_it_is_pr_only():
    """Guard the reason, not just the entry.

    release-guard is `if: github.event_name == 'pull_request'`, so it reports
    `skipped` on every push to main. If it were ever required, main would either
    block permanently or depend on GitHub treating `skipped` as a pass. If that
    condition is ever removed, this fails and the exclusion can be revisited.
    """
    block = _ci_jobs_block()
    guard = block[block.index("  release-guard:") :]
    rest = re.search(r"\n  [a-z][a-z0-9-]*:", guard[len("  release-guard:") :])
    guard = guard[: rest.start()] if rest else guard
    assert "github.event_name == 'pull_request'" in guard, (
        "release-guard is no longer PR-only; it can now be required on main"
    )
    assert "release-guard" in _required_manifest()["excluded"]


def test_concurrency_group_separates_manually_dispatched_runs():
    """A dispatch must not cancel the push run it was dispatched to verify.

    Without the event name in the group, `workflow_dispatch` on main landed in
    the same group as the push run and cancel-in-progress killed it — observed
    on 5f74694, where the push run went to `cancelled`.
    """
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    group = re.search(r"group: (.+)$", ci, re.M)
    assert group, "no concurrency group found in ci.yml"
    assert "github.event_name" in group.group(1), (
        "ci.yml concurrency group omits the event name, so a manual dispatch "
        "cancels the push run on the same ref"
    )
    assert "cancel-in-progress: true" in ci, "superseded runs are no longer cancelled"


def test_watchdog_exists_and_can_reach_the_api():
    """The watchdog is the only thing that notices CI did not run at all."""
    import json

    path = ROOT / ".github/workflows/ci-watchdog.yml"
    assert path.exists(), "ci-watchdog.yml is missing: a missing CI run cannot report itself"
    wf = path.read_text()
    assert "schedule:" in wf and "cron:" in wf, "watchdog never runs on its own"
    assert "actions: read" in wf, "watchdog needs actions:read to list runs"
    # least privilege, per #319
    assert "contents: write" not in wf, "watchdog should not need write access"
    body = wf.split("run: |", 1)[1]
    assert "workflows/ci.yml/runs" in body, "watchdog does not query ci.yml runs"
    # a green run must win even when something else is also attached to the SHA
    assert body.index("completed,success,") < body.index("queued"), (
        "success is no longer checked before the in-flight escape hatch"
    )
    assert json.loads((ROOT / ".github/required-checks.json").read_text())["contexts"]
