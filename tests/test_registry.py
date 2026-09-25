"""Skill registry tests (#50): vendored index, search, offline install."""

import shutil

import pytest

import sk.skills as skills


def _iso(tmp_path, monkeypatch):
    d = tmp_path / "skills"
    d.mkdir()
    monkeypatch.setattr(skills, "SKILLS_DIR", d)
    return d


def test_registry_index_valid():
    assert len(skills.REGISTRY) >= 3
    names = [e["name"] for e in skills.REGISTRY]
    assert len(names) == len(set(names))  # unique
    for e in skills.REGISTRY:
        assert e["description"].strip()
        assert e["url"].startswith("https://github.com/")
        assert " " not in e["name"]


def test_search_registry_ranking_and_empty():
    assert skills.search_registry("") == skills.REGISTRY
    hits = skills.search_registry("anthropic official")
    assert hits and hits[0]["name"] == "anthropic-skills"
    assert skills.search_registry("zzz-no-such-pack") == []
    assert skills.search_registry("??") == skills.REGISTRY  # no keywords -> all
    assert skills.registry_entry("SuperPowers")["url"].endswith("/obra/superpowers")
    assert skills.registry_entry("nope") is None


def test_install_pack_unknown():
    out = skills.install_pack("nope")
    assert "Unknown pack" in out and "superpowers" in out


def test_install_pack_registry_mocked_clone(monkeypatch, tmp_path):
    import subprocess

    _iso(tmp_path, monkeypatch)
    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/git")

    def fake_run(cmd, **k):
        assert "--depth" in cmd and "1" in cmd  # shallow clone
        dest = skills.SKILLS_DIR / "anthropic-skills"
        (dest / "pdf").mkdir(parents=True)
        (dest / "pdf" / "SKILL.md").write_text(
            '---\nname: pdf\ndescription: "PDF docs."\n---\n\n# PDF\n\nFill forms.'
        )
        return type("R", (), {})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = skills.install_pack("anthropic-skills")
    assert "Installed anthropic-skills (1 skills)" in out
    assert "Fill forms" in skills.show_skill("pdf")
    out2 = skills.install_pack("anthropic-skills")
    assert "already" in out2


def test_install_pack_preset_still_works(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    monkeypatch.setattr("shutil.which", lambda b: None)
    assert "git not found" in skills.install_pack("superpowers").lower()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_install_pack_local_git_repo_offline(tmp_path, monkeypatch):
    """End-to-end offline: file:// clone of a scratch repo, no network."""
    import subprocess

    _iso(tmp_path, monkeypatch)
    src = tmp_path / "srcrepo"
    (src / "my-skill").mkdir(parents=True)
    (src / "my-skill" / "SKILL.md").write_text(
        '---\nname: my-skill\ndescription: "Local skill."\n---\n\n# Local\n\nDo things.'
    )
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    subprocess.run(["git", "-C", str(src), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(src),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "init",
        ],
        check=True,
    )
    monkeypatch.setattr(
        skills,
        "REGISTRY",
        [{"name": "localpack", "description": "Scratch pack.", "url": f"file://{src}"}],
    )
    out = skills.install_pack("localpack")
    assert "Installed localpack (1 skills)" in out
    assert "Do things" in skills.show_skill("my-skill")


def test_registry_cli(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso(tmp_path, monkeypatch)
    res = CliRunner().invoke(app, ["skills-registry"])
    assert res.exit_code == 0, res.output
    assert "superpowers" in res.output and "offline" in res.output
    res = CliRunner().invoke(app, ["skills-registry", "anthropic"])
    assert "anthropic-skills" in res.output
    res = CliRunner().invoke(app, ["skills-registry", "zzz-no-such-pack"])
    assert "no matches" in res.output
