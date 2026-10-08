"""Documentation and packaging agree with the code (#370).

Version numbers drift across five places — `__init__.py`, the tags,
`ROADMAP.md`, `website/`, and the AUR `PKGBUILD` — and had already drifted in
every one of them: `pkgver` was three releases behind with a hardcoded sdist
hash for the wrong version, `ROADMAP.md` pointed `Next` at a version that had
already shipped, and the website's roadmap was eight releases stale.

These assert the mechanical agreements. They deliberately do not try to check
that prose is *true* — a doc can be confidently wrong in a way no test can see,
which is why the high-severity ones were fixed by reading the code first.
"""

from __future__ import annotations

import pathlib
import re

import pytest

import sk

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


@pytest.fixture(scope="module")
def version() -> str:
    return sk.__version__


# --- version copies ---------------------------------------------------------


def test_roadmap_next_points_past_the_current_version(version):
    """`Next` pointing at an already-shipped version is how v0.28.0 got burned."""
    next_line = next(
        (ln for ln in _read("ROADMAP.md").splitlines() if ln.startswith("## Next")),
        "",
    )
    assert next_line, "ROADMAP.md has no '## Next' section"
    m = re.search(r"v(\d+)\.(\d+)\.(\d+)", next_line)
    assert m, f"unparsable Next line: {next_line!r}"
    assert m.group(0) > f"v{version}", (
        f"ROADMAP.md says {next_line!r} but v{version} is already the current "
        f"version; Next must point past it"
    )


def test_roadmap_done_records_the_current_version(version):
    done = _read("ROADMAP.md")
    assert f"v{version}" in done, (
        f"ROADMAP.md 'Done' never mentions v{version}; the release checklist in "
        f"CONTRIBUTING.md requires it"
    )


def test_roadmap_documents_the_burned_version():
    """A number that can never be published should say so where Next is derived."""
    assert "v0.28.0" in _read("ROADMAP.md")
    assert "burned" in _read("ROADMAP.md").lower()


def test_website_next_matches_roadmap_next():
    roadmap = _read("ROADMAP.md")
    tag = re.search(r"## Next — (v\d+\.\d+\.\d+)", roadmap)
    assert tag, "could not read the roadmap's Next version"
    assert tag.group(1) in _read("website/src/pages/Roadmap.tsx"), (
        f"website still advertises a different Next version than ROADMAP.md ({tag.group(1)})"
    )


def test_aur_pkgver_tracks_the_source(version):
    """A stale `pkgver` means `yay -Syu` keeps serving an old package, and the
    hardcoded sdist URL + sha256 pin a specific artifact that only matches its own
    version. It was three releases behind with the 0.26.0 hash.

    Strict equality is not the right invariant: the pinned sdist has to exist on
    PyPI, and a release bump necessarily lands before AUR is repinned. So the
    rule is one-sided and bounded -- AUR may not point at a version that does not
    exist yet, and may not lag by more than one minor.
    """
    aur = _read("packaging/aur/PKGBUILD")
    m = re.search(r"^pkgver=(\S+)", aur, re.M)
    assert m, "PKGBUILD has no pkgver"
    pinned = tuple(int(x) for x in m.group(1).split("."))
    current = tuple(int(x) for x in version.split("."))

    assert pinned <= current, (
        f"PKGBUILD pins {m.group(1)}, ahead of the source version {version}; that "
        f"sdist does not exist on PyPI yet, so the package cannot build"
    )
    assert current[1] - pinned[1] <= 1, (
        f"PKGBUILD pins {m.group(1)} but the source is {version} -- AUR has fallen "
        f"more than one minor release behind. Refresh pkgver, the sdist URL and "
        f"the sha256 from https://pypi.org/pypi/sidekick-agent/<version>/json"
    )


def test_aur_source_and_hash_agree_with_pkgver():
    """The sdist path segment and the sha256 are both version-specific.

    The pinned URL is a `files.pythonhosted.org/packages/<a>/<b>/<hash>/…` path,
    and that hash directory is derived from the file, so bumping `pkgver` without
    refreshing the URL and the digest yields a package that cannot build. The
    filename must be templated on `pkgver` (not hardcoded) so the two track.
    """
    aur = _read("packaging/aur/PKGBUILD")

    source = re.search(r'^source=\("\$\{_pkgname\}-\$\{pkgver\}\.tar\.gz::(\S+?)"\)$', aur, re.M)
    assert source, "PKGBUILD source line is not in the expected templated form"
    url = source.group(1)
    assert "${_pkgname}-${pkgver}.tar.gz" in url, f"source URL filename is not templated: {url}"
    assert "files.pythonhosted.org/packages/" in url

    sha = re.search(r'^sha256sums=\("([0-9a-f]{64})"\)$', aur, re.M)
    assert sha, "PKGBUILD needs a 64-hex sha256sums entry for the pinned sdist"
    # A literal version anywhere outside pkgver means one of the two was edited
    # without the other.
    stray = re.findall(r"sidekick[_-]agent-(?!\$\{)(\d+\.\d+\.\d+)", aur)
    assert not stray, (
        f"PKGBUILD hardcodes sdist version(s) {stray} outside ${{pkgver}}; refresh "
        f"pkgver, the URL path and the sha256 together"
    )


# --- documented behaviour that does not exist -------------------------------


def test_mcp_doc_documents_the_keys_that_refuse_tools():
    """Default trust is untrusted, and a refusal tells the user to set it. A key
    named only in an error message is undiscoverable."""
    doc = _read("docs/mcp-client.md")
    assert "`trust`" in doc, "docs/mcp-client.md has no `trust` key documented"
    assert "inherit_env" in doc, "docs/mcp-client.md has no `inherit_env` key documented"
    assert "untrusted" in doc and "readOnlyHint" in doc


def test_plugins_doc_is_not_written_as_a_design_document():
    plugins_doc = _read("docs/plugins.md")
    assert "Status: **shipped**" in plugins_doc, (
        "docs/plugins.md still reads as an unimplemented design doc"
    )
    assert "#49 implements against this doc" not in plugins_doc


def test_plugins_doc_shows_the_param_form_the_parser_accepts():
    """`params` only parses from an inline string. Documenting the mapping form
    produced a tool whose `{slots}` were never substituted, with no warning."""
    plugins_doc = _read("docs/plugins.md")
    assert re.search(r"^params: \w+: (string|integer)", plugins_doc, re.M), (
        "docs/plugins.md does not show the inline params form the parser accepts"
    )
    # And the shipped examples must use it, since the README points users there.
    for ex in (ROOT / "docs" / "examples").glob("*/TOOLS.md"):
        text = ex.read_text()
        assert not re.search(r"^params:\s*$", text, re.M), (
            f"{ex} uses the mapping form, which the parser ignores"
        )


def test_plugins_doc_does_not_promise_a_shell_fallback():
    """There is no escalation from tool_exec to the shell tool; the refusal is
    the whole point."""
    body = _read("docs/plugins.md").split("## Deliberate differences")[0]
    assert "falls back to the `shell` tool, which keeps its approval gate" not in body


def test_egress_doc_no_longer_claims_something_false():
    egress_doc = _read("docs/egress.md")
    assert "on every redirect hop" in egress_doc
    # It must not claim the search tool follows result links.
    assert "every hit it follows" not in egress_doc
    assert "including each search hit" not in _read("README.md")


def test_readme_documents_trust_repo():
    """An undocumented security gate means a user cannot find out why their
    repo's commands silently do nothing."""
    readme = _read("README.md")
    assert "SIDEKICK_TRUST_REPO" in readme


def test_readme_documents_the_version_flag():
    assert "sk --version" in _read("README.md")


def test_contributing_does_not_reference_removed_gates():
    """Both were deleted deliberately; the checklist still asked for them, so a
    release PR would have been judged against gates that no longer exist."""
    contributing = _read("CONTRIBUTING.md")
    assert "Test-count badge in `README.md` matches" not in contributing
    assert "tests × 6, badge, build" not in contributing
    # The checklist must name coverage, which is now a required check.
    assert "coverage" in contributing


def test_readme_command_table_matches_cli():
    """The README command table is hand-maintained and `sk --help` grows by
    PR: a new command with no row (or a row for a removed command) drifts
    silently. Introspect the typer app instead of shelling out, so the test
    stays offline and hermetic."""
    import sk.cli as cli

    cli_cmds: set[str] = set()
    for info in getattr(cli.app, "registered_commands", []):
        if getattr(info, "hidden", False):
            continue  # hook-log, run-bg-worker: intentionally undocumented
        cb = getattr(info, "callback", None)
        name = getattr(info, "name", None) or (getattr(cb, "__name__", "") or "").replace("_", "-")
        if name:
            cli_cmds.add(name)
    groups: dict[str, set[str]] = {}
    for grp in getattr(cli.app, "registered_groups", []):
        subs: set[str] = set()
        inst = getattr(grp, "typer_instance", None)
        for cmd in getattr(inst, "registered_commands", []) or []:
            cb = getattr(cmd, "callback", None)
            nm = getattr(cmd, "name", None) or (getattr(cb, "__name__", "") or "").replace("_", "-")
            if nm:
                subs.add(nm)
        if getattr(grp, "name", None):
            groups[grp.name] = subs
    cli_cmds |= set(groups)
    assert len(cli_cmds) > 40, f"introspection found only {sorted(cli_cmds)}"

    table = _read("README.md").split("## Command reference")[1].split("## Architecture")[0]
    firsts: set[str] = set()  # head command of each `sk ...` chunk
    mentioned: set[str] = set()  # every slash-group segment head
    for chunk in re.findall(r"`(sk [^`]+)`", table):
        for seg in chunk.split("/"):
            tok = re.match(r"\s*(?:sk\s+)?([a-z][a-z-]*)", seg)
            if tok:
                mentioned.add(tok.group(1))
        head = re.match(r"sk\s+([a-z][a-z-]*)", chunk)
        if head:
            firsts.add(head.group(1))
    # every CLI command appears somewhere in the table (slash-group rows like
    # `sk remember/recall/memories/forget` cover several at once)
    assert not (set(cli_cmds) - mentioned), (
        f"commands with no README row: {sorted(set(cli_cmds) - mentioned)}"
    )
    # every documented head command still exists (catches removed-command rows)
    assert not ({f for f in firsts if f not in cli_cmds} - {"sk"}), (
        f"README rows for unknown commands: {sorted(firsts - set(cli_cmds) - {'sk'})}"
    )
    # group subcommands ride in the group row (`sk auth add/list/...`); a
    # renamed subcommand must update that row, not just the code
    rows = [ln for ln in table.splitlines() if ln.startswith("|")]
    for grp, subs in groups.items():
        row = next((ln for ln in rows if f"sk {grp}" in ln), "")
        assert row, f"no README row documents `sk {grp}`"
        assert not (subs - set(re.findall(r"[a-z][a-z-]*", row))), (
            f"`sk {grp}` row is missing subcommands: {sorted(subs - set(re.findall(r'[a-z][a-z-]*', row)))}"
        )


def test_daemon_uninstall_is_documented():
    """`daemon.remove_launchd` existed with no command, so an install could only
    be undone by hand. The command and its doc line must stay together."""
    assert "daemon-uninstall" in _read("README.md")
    assert re.search(r"def daemon_uninstall\(\)", _read("src/sk/cli/commands/daemon.py")), (
        "daemon-uninstall command is gone"
    )


def test_daemon_uninstall_is_reachable_from_the_cli():
    import sk.cli as cli

    names = set()
    for info in getattr(cli.app, "registered_commands", []):
        cb = getattr(info, "callback", None)
        names.add(getattr(cb, "__name__", "") or getattr(info, "name", ""))
    assert "daemon_uninstall" in names, sorted(n for n in names if n)
