"""Shipped skill bundles: every skills/*/SKILL.md in the repo must be valid
and loadable by the app's skill machinery (frontmatter, size caps, index)."""

import shutil
from pathlib import Path

import sk.skills as skills

REPO_SKILLS = Path(__file__).resolve().parents[1] / "skills"


def _bundles():
    return sorted((REPO_SKILLS).glob("*/SKILL.md"))


def test_at_least_one_bundle_shipped():
    assert _bundles(), "skills/ must contain at least one */SKILL.md bundle"


def test_bundle_frontmatter_and_size():
    for f in _bundles():
        text = f.read_text(errors="replace")
        meta, body = skills.parse_frontmatter(text)
        assert meta.get("name"), f"{f}: frontmatter needs a name"
        assert meta.get("description"), f"{f}: frontmatter needs a description"
        assert len(body.strip()) > 200, f"{f}: body looks empty"
        assert f.stat().st_size <= 100_000, f"{f}: app skips SKILL.md over 100KB"


def test_bundle_loads_through_app(monkeypatch, tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    monkeypatch.setattr(skills, "SKILLS_DIR", d)
    skills.ensure_defaults()
    for f in _bundles():
        dest = d / f.parent.name
        shutil.copytree(f.parent, dest)
        meta, _ = skills.parse_frontmatter(f.read_text(errors="replace"))
        out = skills.load_skills()
        assert meta["name"] in out  # index line (app truncates each line to 160 chars)
        assert meta["description"][:100] in out
        assert skills.search_skills("tester")[0][0] == meta["name"]
        body = skills.show_skill(meta["name"])
        assert "name:" not in body.splitlines()[0]  # frontmatter stripped
        assert len(body) > 200
