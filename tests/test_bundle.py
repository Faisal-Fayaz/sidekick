"""The air-gapped bundle: verified offline installs (#475).

The tests use tiny wheels and a test seam for the download, so the suite stays
fast. One real end-to-end build/install is done by hand and recorded in
`docs/install.md`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import sk.bundle as bundle
from sk.cli import app


def _wheel(path: Path, name: str, data: bytes = b"wheel") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    target = path / name
    target.write_bytes(data)
    return target


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    _wheel(root / bundle.WHEELS_DIR, sidekick())
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "sdk.md").write_text("offline docs")
    bundle.write_manifest(root, "9.9.9")
    return root


def sidekick() -> str:
    return "sidekick_agent-9.9.9-py3-none-any.whl"


# --- the manifest ----------------------------------------------------------


def test_a_bundle_verifies_clean(tmp_path):
    root = _bundle(tmp_path)
    ok, problems = bundle.verify_bundle(root)
    assert ok, problems


def test_a_tampered_file_is_refused(tmp_path):
    root = _bundle(tmp_path)
    (root / "docs" / "sdk.md").write_text("tampered")
    ok, problems = bundle.verify_bundle(root)
    assert not ok
    assert any("hash mismatch" in p for p in problems), problems


def test_a_missing_file_is_refused(tmp_path):
    root = _bundle(tmp_path)
    (root / "docs" / "sdk.md").unlink()
    ok, problems = bundle.verify_bundle(root)
    assert not ok
    assert any("missing" in p for p in problems), problems


def test_an_undeclared_file_is_refused(tmp_path):
    root = _bundle(tmp_path)
    (root / "surprise.txt").write_text("not in the manifest")
    ok, problems = bundle.verify_bundle(root)
    assert not ok
    assert any("undeclared" in p for p in problems), problems


def test_a_bundle_without_a_manifest_is_refused(tmp_path):
    ok, problems = bundle.verify_bundle(tmp_path / "nope")
    assert not ok
    assert problems


def test_the_manifest_records_every_file_with_a_hash_and_size(tmp_path):
    root = _bundle(tmp_path)
    manifest = bundle.read_manifest(root)
    assert manifest["version"] == "9.9.9"
    assert manifest["size"] > 0
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])
    assert any(item["path"].endswith(".whl") for item in manifest["files"])
    assert "not included" in manifest["models"]


def test_sha256_file_is_a_real_hash(tmp_path):
    path = tmp_path / "x.txt"
    path.write_bytes(b"abc")
    assert bundle.sha256_file(path) == hashlib.sha256(b"abc").hexdigest()


# --- building --------------------------------------------------------------


def test_a_build_uses_the_locked_requirements(tmp_path, monkeypatch):
    """The bundle holds exactly the versions the lockfile resolved."""
    import sk.bundle as mod

    seen = {}

    def fake_run(argv, *, cwd=None):
        seen["argv"] = argv
        if argv[:2] == ["uv", "export"]:
            return 0, "httpx==0.28.1\nrich==15.0.0\n", ""
        if argv[:2] == ["uv", "build"]:
            _wheel(tmp_path / "built" / bundle.WHEELS_DIR, sidekick())
            return 0, "", ""
        if argv[:2] == ["uvx", "pip"]:
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(mod, "_run", fake_run)
    source = tmp_path / "source"
    source.mkdir()
    (source / "skills").mkdir()
    (source / "LICENSE").write_text("MIT")

    out = tmp_path / "out"
    mod.build_bundle(out, source=source)
    assert seen["argv"][:2] == ["uvx", "pip"]  # the last call was the download
    assert (out / "skills").is_dir()
    assert (out / "LICENSE").is_file()
    ok, problems = mod.verify_bundle(out)
    assert ok, problems


def test_locked_requirements_skips_comments_and_options(tmp_path, monkeypatch):
    import sk.bundle as mod

    monkeypatch.setattr(
        mod,
        "_run",
        lambda argv, *, cwd=None: (
            0,
            "# comment\n--index-url https://x\nfoo==1.0\nbar==2.0 ; python_version > '3'\n",
            "",
        ),
    )
    assert mod.locked_requirements(tmp_path) == [
        "foo==1.0",
        "bar==2.0 ; python_version > '3'",
    ]


# --- installing offline ----------------------------------------------------


def test_install_verifies_before_anything_else(tmp_path, monkeypatch):
    root = _bundle(tmp_path)
    (root / "docs" / "sdk.md").write_text("tampered")
    calls = []
    monkeypatch.setattr(bundle, "_run", lambda argv, **kw: calls.append(argv) or (0, "", ""))
    ok, message = bundle.install_bundle(root, tmp_path / "venv")
    assert not ok
    assert "verification" in message
    assert calls == [], "a tampered bundle was installed"


def test_install_uses_no_index_and_only_the_bundled_wheels(tmp_path, monkeypatch):
    root = _bundle(tmp_path)
    calls = []
    monkeypatch.setattr(bundle, "_run", lambda argv, **kw: calls.append(argv) or (0, "", ""))

    ok, message = bundle.install_bundle(root, tmp_path / "venv")
    assert ok, message
    install = [c for c in calls if c[:2] == ["uv", "pip"]][0]
    assert "--no-index" in install
    assert "--find-links" in install
    assert str(root / bundle.WHEELS_DIR) in install
    assert "sidekick-agent==9.9.9" in install


def test_install_reports_a_failed_offline_install(tmp_path, monkeypatch):
    root = _bundle(tmp_path)

    def fake_run(argv, **kw):
        if argv[:2] == ["uv", "pip"]:
            return 1, "", "no matching distribution found"
        return 0, "", ""

    monkeypatch.setattr(bundle, "_run", fake_run)
    ok, message = bundle.install_bundle(root, tmp_path / "venv")
    assert not ok
    assert "offline install failed" in message


# --- the CLI ---------------------------------------------------------------


def test_the_bundle_commands_exist():
    for command in ("build", "verify", "install"):
        result = CliRunner().invoke(app, ["bundle", command, "--help"])
        assert result.exit_code == 0, (command, result.output)


def test_verify_cli_reports_a_tampered_bundle(tmp_path):
    root = _bundle(tmp_path)
    (root / "docs" / "sdk.md").write_text("tampered")
    result = CliRunner().invoke(app, ["bundle", "verify", "--in", str(root)])
    assert result.exit_code == 1
    assert "hash mismatch" in result.output
