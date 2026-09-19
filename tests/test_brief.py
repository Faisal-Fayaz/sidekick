"""Brief tests: no Ollama/LLM needed (sysinfo may degrade gracefully)."""

from sk.brief import gather_brief, git_snapshot


def test_git_snapshot_missing():
    s = git_snapshot("~/definitely-not-a-real-dir-xyz")
    assert s["exists"] is False


def test_gather_brief_shape():
    data = gather_brief(["~/sidekick"])
    assert "when" in data and "sysinfo" in data and "projects" in data and "memories" in data
    assert isinstance(data["projects"], list) and len(data["projects"]) == 1
