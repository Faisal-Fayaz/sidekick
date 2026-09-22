"""Skills tests: isolated SKILLS_DIR."""

import sk.skills as skills


def _iso(tmp_path, monkeypatch):
    d = tmp_path / "skills"
    d.mkdir()
    monkeypatch.setattr(skills, "SKILLS_DIR", d)
    return d


def test_defaults_created(monkeypatch, tmp_path):
    d = _iso(tmp_path, monkeypatch)
    skills.ensure_defaults()
    assert (d / "git.md").exists() and (d / "disk.md").exists()


def test_frontmatter_and_bundle(monkeypatch, tmp_path):
    d = _iso(tmp_path, monkeypatch)
    skills.ensure_defaults()
    pack = d / "debugging"
    pack.mkdir()
    (pack / "SKILL.md").write_text(
        '---\nname: systematic-debugging\ndescription: "Find root causes."\n---\n\n# Debug\n\nSteps here.'
    )
    out = skills.load_skills()
    assert "systematic-debugging" in out and "Find root causes." in out
    body = skills.show_skill("systematic-debugging")
    assert "Steps here" in body and "name:" not in body  # frontmatter stripped
    assert "Unknown skill" in skills.show_skill("nope")


def test_load_caps_and_local_bodies(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    skills.ensure_defaults()
    out = skills.load_skills()
    assert "SKILL INDEX" in out and "git" in out
    assert len(out) <= 2500


def test_install_unknown_and_no_git(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    assert "Unknown preset" in skills.install_preset("nope")
    monkeypatch.setattr("shutil.which", lambda b: None)
    assert "git not found" in skills.install_preset("superpowers").lower()


def test_install_mocked_clone(monkeypatch, tmp_path):
    import subprocess

    _iso(tmp_path, monkeypatch)
    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/git")

    def fake_run(*a, **k):
        (tmp_path / "skills" / "superpowers" / "x").mkdir(parents=True)
        (tmp_path / "skills" / "superpowers" / "x" / "SKILL.md").write_text("# x")
        return type("R", (), {})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = skills.install_preset("superpowers")
    assert "Installed superpowers" in out
    out2 = skills.install_preset("superpowers")
    assert "already" in out2


def test_search_ranking_and_empty(monkeypatch, tmp_path):
    d = _iso(tmp_path, monkeypatch)
    skills.ensure_defaults()
    pack = d / "debugging"
    pack.mkdir()
    (pack / "SKILL.md").write_text(
        '---\nname: systematic-debugging\ndescription: "Find root causes."\n---\n\n# Debug\n\nSteps here.'
    )
    hits = skills.search_skills("root causes")
    assert hits and hits[0][0] == "systematic-debugging"
    # name match outranks description-only match
    hits = skills.search_skills("debugging")
    assert hits[0][0] == "systematic-debugging"
    # empty query lists everything (defaults + bundle)
    assert {n for n, _ in skills.search_skills("")} >= {"git", "disk", "systematic-debugging"}
    # no match, no crash
    assert skills.search_skills("zzzqqq xxxwww") == []


def test_search_cli(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso(tmp_path, monkeypatch)
    skills.ensure_defaults()
    res = CliRunner().invoke(app, ["skills-search"])
    assert res.exit_code == 0 and "git" in res.output
    res = CliRunner().invoke(app, ["skills-search", "zzzqqq xxxwww"])
    assert res.exit_code == 0 and "no matches" in res.output.lower()
