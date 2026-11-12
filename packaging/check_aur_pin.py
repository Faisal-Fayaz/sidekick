"""Check that the AUR `PKGBUILD` pin matches a published artifact (#443).

The AUR package pins a version, an sdist URL, and a sha256. All three can rot
silently: the version moves, the URL points at the old artifact, or the hash
belongs to a different file. `yay -Syu` then serves an old package, or one
that does not build, and nothing says so.

This compares the pin against what PyPI actually has. It is the drift check
the release smoke test runs — the Arch package itself is built and installed
by the AUR, but the pin it is built from has to be right first.

    python3 packaging/check_aur_pin.py                 # the latest release
    python3 packaging/check_aur_pin.py --version 0.33.0
    python3 packaging/check_aur_pin.py --json local.json   # offline, for tests
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

PKGBUILD = Path(__file__).resolve().parent / "aur" / "PKGBUILD"
PYPI_URL = "https://pypi.org/pypi/sidekick-agent/{version}/json"


def pypi_release(version: str) -> dict:
    """The PyPI metadata for one release."""
    url = PYPI_URL.format(version=version)
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def sdist(release: dict) -> dict:
    """The sdist entry of a release — the artifact the AUR builds from."""
    for entry in release.get("urls") or []:
        if entry.get("packagetype") == "sdist":
            return entry
    raise SystemExit("no sdist in the release metadata")


def read_pin(path: Path = PKGBUILD) -> dict:
    """The version, source URL, and sha256 the PKGBUILD pins."""
    text = path.read_text()
    pkgver = re.search(r"^pkgver=(\S+)", text, re.M)
    source = re.search(r'^source=\("\$\{_pkgname\}-\$\{pkgver\}\.tar\.gz::([^"]+)"\)', text, re.M)
    sha = re.search(r'^sha256sums=\("([0-9a-f]{64})"\)', text, re.M)
    return {
        "pkgver": pkgver.group(1) if pkgver else "",
        "source": source.group(1) if source else "",
        "sha256": sha.group(1) if sha else "",
        "path": str(path),
    }


def same_source(pin_url: str, pypi_url: str, version: str) -> bool:
    """Whether the pinned source points at the published artifact.

    The PKGBUILD writes the URL as a template (`${_pkgname}-${pkgver}.tar.gz`),
    and PyPI normalises the file name to underscores where the package name has
    them, so the two are compared on the directory and on the name with the
    separator normalised.
    """
    expanded = pin_url.replace("${_pkgname}", "sidekick-agent").replace("${pkgver}", version)
    pin_dir, _, pin_name = expanded.rpartition("/")
    pypi_dir, _, pypi_name = pypi_url.rpartition("/")
    if pin_dir != pypi_dir:
        return False
    return pin_name.replace("_", "-") == pypi_name.replace("_", "-")


def compare(pin: dict, release: dict) -> list[str]:
    """The problems between the pin and the published artifact. Empty = good."""
    problems: list[str] = []
    version = str(release.get("info", {}).get("version") or "")
    if pin["pkgver"] != version:
        problems.append(f"pkgver is {pin['pkgver']}, PyPI has {version}")
    try:
        artifact = sdist(release)
    except SystemExit as e:
        return [str(e)]
    url = str(artifact.get("url") or "")
    if url and not same_source(pin["source"], url, version):
        problems.append(
            "the source URL does not match the published artifact\n"
            f"  pin:  {pin['source']}\n  pypi: {url}"
        )
    digest = str((artifact.get("digests") or {}).get("sha256") or "")
    if digest and digest != pin["sha256"]:
        problems.append(f"sha256 is {pin['sha256']}, the artifact hashes to {digest}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="", help="release to check (default: the pin's)")
    parser.add_argument("--json", default="", help="read the release metadata from a file")
    parser.add_argument("--pkgbuild", default=str(PKGBUILD))
    args = parser.parse_args(argv)

    pin = read_pin(Path(args.pkgbuild))
    version = args.version or pin["pkgver"]
    if not version:
        print("no version to check", file=sys.stderr)
        return 2

    if args.json:
        release = json.loads(Path(args.json).read_text())
    else:
        try:
            release = pypi_release(version)
        except Exception as e:
            print(f"could not read PyPI metadata for {version}: {e}", file=sys.stderr)
            return 2

    problems = compare(pin, release)
    if problems:
        print(f"AUR pin does not match {version}:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("Refresh packaging/aur/PKGBUILD from the PyPI JSON.", file=sys.stderr)
        return 1
    print(f"AUR pin matches {version} (pkgver, source URL, sha256).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
