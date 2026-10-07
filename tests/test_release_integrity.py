"""Release integrity (#370).

Nothing in the repo checked that a version number could actually be released,
that the source version tracked the tags, or that the release workflow could
finish once it had uploaded. All three had already failed in practice:

- v0.28.0 existed in `src/sk/__init__.py` for three minutes and was never
  tagged or published. CONTRIBUTING.md calls that "a documented anti-pattern,
  not a procedure", and nothing enforced it.
- The `github-release` job required `needs.publish.result == 'success'`, but a
  re-run after a successful upload sets `publish=false` and skips the `publish`
  job, so the condition could never hold again — PyPI had the release, the repo
  had no tag, and there was no way back.
- `sk --version` did not exist, and `sk version` printed only a git hash, so
  the semantic version was unreachable from inside a checkout.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

import sk
from sk.cli import _full_version

ROOT = pathlib.Path(__file__).resolve().parent.parent
INIT_PY = ROOT / "src" / "sk" / "__init__.py"
RELEASE_YML = (ROOT / ".github" / "workflows" / "release.yml").read_text()

TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

# Version numbers that were written into the source and then superseded before
# being tagged or published. They can never be released — PyPI forbids
# republishing a number — so the sequence test treats them as known. Each entry
# is a permanent gap; adding one here is a decision, not an accident, which is
# the point: an unlisted gap fails the test.
BURNED_VERSIONS = {
    "0.1.0",  # initial dev bump, superseded before the first tag
    "0.14.0",  # tagged in code but never reached PyPI (see CONTRIBUTING.md)
    "0.28.0",  # the three-minute bump, #370
}


def _tags() -> list[str]:
    try:
        r = subprocess.run(
            ["git", "tag", "--list"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception:
        pytest.skip("git unavailable")
    return [t for t in (r.stdout or "").split() if TAG_RE.match(t)]


def _version_tuple(v: str) -> tuple[int, int, int]:
    m = TAG_RE.match(f"v{v}")
    assert m, f"{v!r} is not a valid vX.Y.Z version"
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


# --- the version number must be releasable ----------------------------------


def test_tags_are_well_formed():
    """Every tag matches vX.Y.Z exactly — nothing like `0.30` or `v0.30.0-rc1`."""
    raw = subprocess.run(
        ["git", "tag", "--list"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    ).stdout.split()
    bad = [t for t in raw if not TAG_RE.match(t)]
    assert not bad, f"malformed tags: {bad}"


def test_no_duplicate_version_numbers():
    versions = [t[1:] for t in _tags()]
    dupes = {v for v in versions if versions.count(v) > 1}
    assert not dupes, f"duplicate version numbers: {sorted(dupes)}"


def test_no_unaccounted_gaps_in_the_release_sequence():
    """Minor and patch numbers run consecutively.

    A skipped number is permanently un-publishable on PyPI, which is exactly why
    v0.14.0 and v0.28.0 can never be released. Those are listed in
    BURNED_VERSIONS; anything else missing from the sequence is a new burn and
    must fail here rather than be discovered when the release job runs.
    """
    seen = sorted({_version_tuple(t[1:]) for t in _tags()})
    assert seen, "no tags found — cannot check the sequence"

    minor_seen = {m for _, m, _ in seen}
    minor_max = max(minor_seen)
    missing_minors = [(0, m, 0) for m in range(1, minor_max + 1) if m not in minor_seen]
    for gap in missing_minors:
        dotted = f"{gap[0]}.{gap[1]}.{gap[2]}"
        assert dotted in BURNED_VERSIONS, (
            f"release sequence skips {dotted} and it is not in BURNED_VERSIONS; "
            f"that number can never be published on PyPI"
        )

    for major, minor, _ in seen:
        patches = sorted(p for ma, mi, p in seen if (ma, mi) == (major, minor))
        expected = set(range(0, max(patches) + 1))
        for p in sorted(expected - set(patches)):
            dotted = f"{major}.{minor}.{p}"
            assert dotted in BURNED_VERSIONS, (
                f"release sequence skips {dotted} within the {major}.{minor} series"
            )


def test_source_version_is_never_behind_the_highest_tag():
    """`__version__` must not trail a tag that has already shipped.

    A bump that moves backwards means the next release silently skips every
    version a user could have installed.
    """
    tags = _tags()
    if not tags:
        pytest.skip("no tags")
    highest = max(_version_tuple(t[1:]) for t in tags)
    current = _version_tuple(sk.__version__)
    assert current >= highest, (
        f"src/sk/__init__.py says {sk.__version__} but v"
        f"{highest[0]}.{highest[1]}.{highest[2]} is already tagged and shipped"
    )


def test_hatch_reads_the_version_from_the_one_place_we_edit_it():
    """pyproject must not hardcode a second copy of the number."""
    pp = (ROOT / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in pp
    assert "[tool.hatch.version]" in pp
    assert 'path = "src/sk/__init__.py"' in pp
    # A literal version in [project] would silently win over the dynamic value.
    project_block = pp.split("[project]", 1)[1].split("\n[", 1)[0]
    assert not re.search(r'^version\s*=\s*"', project_block, re.M)


def test_version_is_the_only_declaration_in_the_source():
    src = INIT_PY.read_text()
    found = re.findall(r'^__version__\s*=\s*"([^"]+)"', src, re.M)
    assert found == [sk.__version__], f"expected one __version__, found {found}"


# --- the workflow has to be able to finish -----------------------------------


def test_github_release_runs_when_the_version_is_already_on_pypi():
    """The recovery path: PyPI has it, so `publish` is skipped, so a condition
    of `needs.publish.result == 'success'` can never hold again."""
    job = RELEASE_YML.split("  github-release:", 1)[1]
    assert "needs.build.outputs.publish == 'false'" in job, (
        "github-release must also run when the version is already on PyPI, "
        "otherwise a failed tag step after a successful upload is unrecoverable"
    )
    assert "continue-on-error" in job, (
        "the artifact download must be optional: on the recovery path nothing "
        "was built, so requiring it is what makes recovery unreachable"
    )
    assert "fail_on_unmatched_files: false" in job


def test_release_trigger_has_a_manual_recovery_path():
    """A recovery that can only fire on the next version bump is not a recovery."""
    assert "reconcile:" in RELEASE_YML
    assert "inputs.reconcile == 'true'" in RELEASE_YML


def test_release_is_not_triggered_by_a_bare_tag_push():
    """Documented consequence: `on.push` filters on branches, so pushing a tag
    by hand runs nothing. The reconcile input exists because of that."""
    on_block = RELEASE_YML.split("\non:", 1)[1].split("\npermissions:", 1)[0]
    assert "branches: [main]" in on_block
    assert "tags:" not in on_block, (
        "a tag push is not a supported release path; reconcile covers that case"
    )


def test_version_is_extracted_from_the_single_source():
    line = next(
        (ln.strip() for ln in RELEASE_YML.splitlines() if "VERSION=" in ln and "sed" in ln),
        None,
    )
    assert line, "the workflow no longer extracts a version"
    assert "__version__" in line and "src/sk/__init__.py" in line, (
        f"version extraction moved off the single source: {line!r}"
    )


# --- the CLI can report the version ------------------------------------------


def test_version_flag_reports_the_semantic_version():
    from typer.testing import CliRunner

    from sk.cli import app

    r = CliRunner().invoke(app, ["--version"])
    assert r.exit_code == 0, r.output
    assert sk.__version__ in r.output


def test_version_command_and_flag_agree():
    """They used to disagree: no flag at all, and `sk version` printed only a
    git hash, so the semantic version was unreachable inside a checkout."""
    from typer.testing import CliRunner

    from sk.cli import app

    flag = CliRunner().invoke(app, ["--version"])
    cmd = CliRunner().invoke(app, ["version"])
    assert flag.exit_code == cmd.exit_code == 0
    assert sk.__version__ in flag.output and sk.__version__ in cmd.output
    assert _full_version().startswith(sk.__version__)
