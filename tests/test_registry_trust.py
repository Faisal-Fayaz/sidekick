"""Checksum-pinned remote packs: verify before the pack lands (#527)."""

from __future__ import annotations

import json
import subprocess

import pytest
from typer.testing import CliRunner

import sk.registry_trust as trust
import sk.skills as skills
from sk.cli import app

PIN = {
    "commit": "a" * 40,
    "sha256": "",  # filled from the fixture content below
    "files": "1",
}
BODY = "---\nname: demo\ndescription: Demo.\n---\n\nDo the thing.\n"


def _pin_for(root):
    import hashlib

    digest = hashlib.sha256()
    rel = "demo/SKILL.md"
    digest.update(rel.encode())
    digest.update(b"\0")
    digest.update(BODY.encode())
    digest.update(b"\0")
    return {**PIN, "sha256": digest.hexdigest()}


def _clone_ok(url, dest):
    from pathlib import Path

    path = Path(dest)
    (path / "demo").mkdir(parents=True, exist_ok=True)
    (path / "demo" / "SKILL.md").write_text(BODY)
    (path / ".git").mkdir(exist_ok=True)
    (path / ".git" / "HEAD").write_text("a" * 40 + "\n")
    return None


def _clone_wrong(url, dest):
    from pathlib import Path

    path = Path(dest)
    (path / "demo").mkdir(parents=True, exist_ok=True)
    (path / "demo" / "SKILL.md").write_text("different instructions")
    (path / ".git").mkdir(exist_ok=True)
    (path / ".git" / "HEAD").write_text("b" * 40 + "\n")
    return None


def _runner(commit):
    class Result:
        returncode = 0
        stdout = commit + "\n"
        stderr = ""

    return lambda argv: Result()


def test_manifest_hash_is_deterministic_and_content_sensitive(tmp_path):
    root = tmp_path / "pack"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "SKILL.md").write_text(BODY)
    first = trust.manifest_hash(root)
    assert first == trust.manifest_hash(root)
    (root / "demo" / "SKILL.md").write_text(BODY + "\nchanged")
    assert trust.manifest_hash(root) != first


def test_verify_accepts_a_matching_checkout(tmp_path):
    root = tmp_path / "pack"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "SKILL.md").write_text(BODY)
    pin = _pin_for(root)
    assert trust.verify(root, pin, runner=_runner("a" * 40)) == ""


def test_verify_refuses_a_wrong_commit_or_manifest(tmp_path):
    root = tmp_path / "pack"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "SKILL.md").write_text(BODY)
    pin = _pin_for(root)
    assert "commit mismatch" in trust.verify(root, pin, runner=_runner("b" * 40))
    (root / "demo" / "SKILL.md").write_text("tampered")
    assert "manifest mismatch" in trust.verify(root, pin, runner=_runner("a" * 40))


def test_clone_verified_moves_a_matching_pack(tmp_path):
    dest = tmp_path / "skills" / "demo"
    assert (
        trust.clone_verified(
            "url", dest, _pin_for(tmp_path), clone=_clone_ok, verify_runner=_runner("a" * 40)
        )
        is None
    )
    assert (dest / "demo" / "SKILL.md").read_text() == BODY
    assert not [p for p in dest.parent.iterdir() if p.name.startswith(".sk-clone-")]


def test_clone_verified_leaves_nothing_on_mismatch(tmp_path):
    dest = tmp_path / "skills" / "demo"
    err = trust.clone_verified(
        "url", dest, _pin_for(tmp_path), clone=_clone_wrong, verify_runner=_runner("a" * 40)
    )
    assert "Refused" in err and "mismatch" in err
    assert not dest.exists()
    assert not [p for p in dest.parent.iterdir() if p.name.startswith(".sk-clone-")]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(skills, "_clone", _clone_ok)
    monkeypatch.setattr(trust, "verify", lambda root, pin, **kw: "")
    monkeypatch.setitem(
        trust.PINS,
        "demo-pinned",
        _pin_for(tmp_path),
    )
    monkeypatch.setattr(
        skills,
        "REGISTRY",
        [
            {"name": "demo-pinned", "description": "Pinned.", "url": "https://example.com/p"},
            {"name": "demo-raw", "description": "Raw.", "url": "https://example.com/q"},
        ],
    )
    return tmp_path


def test_install_pack_verifies_a_pin(isolated):
    out = skills.install_pack("demo-pinned")
    assert "Installed demo-pinned" in out
    assert (isolated / "skills" / "demo-pinned" / "demo" / "SKILL.md").is_file()


def test_unpinned_sources_need_an_explicit_opt_in(isolated):
    out = skills.install_pack("demo-raw")
    assert "Refused" in out and "--allow-unpinned" in out
    out = skills.install_pack("https://example.com/q", allow_unpinned=True)
    assert "Installed custom" in out


def test_pin_for_local_reads_a_real_git_checkout(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "SKILL.md").write_text(BODY)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "x",
        ],
        check=True,
    )
    pin = trust.pin_for_local(tmp_path)
    assert len(pin["commit"]) == 40 and len(pin["sha256"]) == 64 and pin["files"] == "1"


def test_registry_cli_shows_pin_status():
    result = CliRunner().invoke(app, ["skills-registry"])
    assert result.exit_code == 0, result.output
    assert "pinned" in result.output


def test_skills_pin_cli(tmp_path):
    result = CliRunner().invoke(app, ["skills-pin", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert set(json.loads(result.output)) == {"commit", "sha256", "files"}


def test_install_help_has_allow_unpinned():
    import re

    result = CliRunner().invoke(app, ["skills-install", "--help"])
    # Rich wraps option names at the terminal width (80 in CI), so compare
    # after stripping ANSI codes and whitespace rather than raw text.
    plain = re.sub(r"\s+", "", re.sub(r"\x1b\[[0-9;]*m", "", result.output))
    assert "--allow-unpinned" in plain
