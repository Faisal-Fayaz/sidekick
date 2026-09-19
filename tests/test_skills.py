"""Skills tests: isolated HOME."""

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


def test_load_caps(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    skills.ensure_defaults()
    out = skills.load_skills()
    assert "git" in out and len(out) <= 2000


def test_custom_skill(monkeypatch, tmp_path):
    d = _iso(tmp_path, monkeypatch)
    skills.ensure_defaults()
    (d / "mine.md").write_text("# mine\n- rule one")
    assert "mine" in skills.load_skills()
    assert any(n == "mine" for n, _ in skills.list_skills())
