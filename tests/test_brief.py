"""Brief tests: no Ollama/LLM needed (sysinfo may degrade gracefully)."""

from sk.brief import gather_brief, git_snapshot


def test_git_snapshot_missing():
    s = git_snapshot("~/definitely-not-a-real-dir-xyz")
    assert s["exists"] is False


def test_gather_brief_shape():
    data = gather_brief(["~/sidekick"])
    assert "when" in data and "sysinfo" in data and "projects" in data and "memories" in data
    assert isinstance(data["projects"], list) and len(data["projects"]) == 1


def test_git_snapshot_existing_non_repo(tmp_path):
    s = git_snapshot(str(tmp_path))
    assert s["exists"] is True
    assert s["branch"] == "(detached)"
    assert s["changed"] == 0


def test_gather_brief_uses_default_projects_for_empty_list(monkeypatch):
    monkeypatch.setattr("sk.brief.DEFAULT_PROJECTS", ["~/missing-default-project"])
    data = gather_brief([])
    assert len(data["projects"]) == 1
    assert data["projects"][0]["path"] == "~/missing-default-project"
    assert data["projects"][0]["exists"] is False
