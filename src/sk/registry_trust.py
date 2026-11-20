"""Checksum pins for vendored skill packs (#527).

A registry entry's commit and manifest hash are recorded here, computed from
the upstream at pin time. Installation clones into a temporary directory and
verifies **before** the result is moved into `~/.sidekick/skills/`. A mismatch
removes the temp directory and refuses the install: nothing partially lands.

No new runtime dependency — verification uses the git CLI the installer already
requires and `hashlib` from the stdlib.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

# Pinned from the upstream HEAD on 2026-11-20. Refresh deliberately with
# `sk skills-pin NAME` and a reviewable commit; never auto-update on install.
PINS: dict[str, dict[str, str]] = {
    "superpowers": {
        "commit": "22f0b463e7a5e579904dd6211a520321035526f7",
        "sha256": "c6958228f5aa0539d1215333a8a7e8649bfcfeef508c6bf261ec47ad82a9d092",
        "files": "15",
    },
    "anthropic-skills": {
        "commit": "e4bb5d960d87126ff3b3a8af1a104b7db67560ba",
        "sha256": "f0e08052d16ee03e0f5d421c98079d2d5331a3eb7d7f6713f260927ba381c7f1",
        "files": "20",
    },
    "wshobson-agents": {
        "commit": "42203085cfc5736204bf0f6d436dff7e1da0bf3f",
        "sha256": "36f022e30024e0900c9c3fda58aa5f2f6c727cdb13a93d17dcb4ae62fa1a8671",
        "files": "185",
    },
}


def manifest_hash(root: str | Path) -> str:
    """sha256 over sorted SKILL.md paths and bytes. Deterministic, stdlib only."""
    base = Path(root)
    digest = hashlib.sha256()
    for path in sorted(p for p in base.rglob("SKILL.md") if p.is_file()):
        digest.update(str(path.relative_to(base)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def pin_for_local(root: str | Path) -> dict[str, str]:
    """Compute a pin for an existing checkout (maintainer tool)."""
    base = Path(root)
    commit = ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(base),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode == 0:
            commit = result.stdout.strip()
    except Exception:
        pass
    files = [p for p in base.rglob("SKILL.md") if p.is_file()]
    return {"commit": commit, "sha256": manifest_hash(base), "files": str(len(files))}


def verify(root: str | Path, pin: dict[str, str], *, runner: Runner | None = None) -> str:
    """Return "" when the checkout matches its pin, else a refusal reason."""
    base = Path(root)
    actual_commit = ""
    run = runner or (
        lambda argv: subprocess.run(
            argv, cwd=str(base), capture_output=True, text=True, timeout=30, check=False
        )
    )
    try:
        result = run(["git", "rev-parse", "HEAD"])
        if result.returncode == 0:
            actual_commit = result.stdout.strip()
    except Exception as e:  # noqa: BLE001 - an unverifiable checkout is a refusal
        return f"could not verify commit: {type(e).__name__}"
    if pin.get("commit") and actual_commit != pin["commit"]:
        return f"commit mismatch: expected {pin['commit']}, got {actual_commit or '(none)'}"
    actual_hash = manifest_hash(base)
    if pin.get("sha256") and actual_hash != pin["sha256"]:
        return f"manifest mismatch: expected {pin['sha256']}, got {actual_hash}"
    return ""


def clone_verified(
    url: str,
    dest: Path,
    pin: dict[str, str],
    *,
    clone: Callable[[str, Path], str | None],
    verify_runner: Runner | None = None,
) -> str | None:
    """Clone to a sibling temp dir, verify, then move to `dest`. None on success.

    `clone` is injected for tests and matches `skills._clone`'s contract:
    it receives the URL and destination and returns an error string or None.
    A failed verification leaves no directory behind at `dest`.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".sk-clone-{dest.name}-", dir=str(dest.parent)))
    try:
        err = clone(url, temp)
        if err:
            return err
        refusal = verify(temp, pin, runner=verify_runner)
        if refusal:
            return f"Refused: {refusal}. The pack was not installed."
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.move(str(temp), str(dest))
        return None
    finally:
        shutil.rmtree(temp, ignore_errors=True)
