"""Project memory auto-discovery + /init tests (fixes #105). Offline
(git-binary tests skip when git is missing)."""

import shutil
import subprocess

import pytest

import sk.memory_files as mf
import sk.slash as slash
import sk.store as store
from sk.agent import _project_docs_block, build_messages
from sk.config import Config

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _cfg(**kw):
    base = {
        "provider": "ollama",
        "model": "t",
        "base_url": "http://x/v1",
        "api_key": "x",
        "max_steps": 1,
        "temperature": 0.0,
    }
    base.update(kw)
    return Config(**base)


def _sctx(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    return {"session": "test", "cfg": _cfg(), "state": {"yolo": False, "readonly": False}}


def test_priority_sidekick_first(tmp_path):
    (tmp_path / "SIDEKICK.md").write_text("side")
    (tmp_path / "AGENTS.md").write_text("agents")
    (tmp_path / "CLAUDE.md").write_text("claude")
    found = mf.discover_memory_files(str(tmp_path))
    assert [p.name for p in found] == ["SIDEKICK.md"]


def test_priority_agents_over_claude(tmp_path):
    (tmp_path / "AGENTS.md").write_text("agents")
    (tmp_path / "GEMINI.md").write_text("gemini")
    found = mf.discover_memory_files(str(tmp_path))
    assert [p.name for p in found] == ["AGENTS.md"]


@needs_git
def test_root_down_order_and_stop_at_git_root(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    (tmp_path / "AGENTS.md").write_text("root law")
    (tmp_path / "OUTSIDE.md").write_text("above root")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "AGENTS.md").write_text("sub law")
    above = tmp_path.parent / "SIDEKICK.md"
    above.write_text("should be ignored")
    try:
        found = mf.discover_memory_files(str(sub))
        assert found == [tmp_path / "AGENTS.md", sub / "AGENTS.md"]
    finally:
        above.unlink(missing_ok=True)


def test_non_repo_reads_cwd_only(tmp_path):
    sub = tmp_path / "proj"
    sub.mkdir()
    (tmp_path / "AGENTS.md").write_text("parent law")
    (sub / "CLAUDE.md").write_text("cwd law")
    assert mf.discover_memory_files(str(sub)) == [sub / "CLAUDE.md"]


def test_oversize_skipped_and_budget(tmp_path):
    big = tmp_path / "AGENTS.md"
    big.write_text("x" * (mf.MAX_FILE_BYTES + 1))
    assert mf.discover_memory_files(str(tmp_path)) == []
    a = tmp_path / "a"
    a.mkdir()
    (a / "AGENTS.md").write_text("y" * 100)
    out = mf.render_memory_files([a / "AGENTS.md"], budget=10)
    assert out.split("\n", 1)[1] == "y" * 10  # budget caps file text, not the [path] label


def test_merge_explicit_and_discovered(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "explicit.md").write_text("explicit law")
    (proj / "AGENTS.md").write_text("discovered law")
    monkeypatch.chdir(proj)
    cfg = _cfg()
    cfg.project_root = str(proj)
    cfg.project_docs = ("explicit.md",)
    out = _project_docs_block(cfg)
    assert "explicit law" in out and "discovered law" in out
    assert out.index("[explicit.md]") < out.index(str(proj / "AGENTS.md"))


def test_block_none_when_nothing(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    cfg = _cfg()
    cfg.project_root = str(empty)
    cfg.project_docs = ()
    assert _project_docs_block(cfg) == "(none)"


def test_discovered_in_system_prompt(tmp_path, monkeypatch):
    _sctx(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "AGENTS.md").write_text("project law: always haiku")
    monkeypatch.chdir(proj)
    system = build_messages("hi", [], _cfg())[0]["content"]
    assert "project law" in system


def test_init_scaffolds_with_facts(tmp_path, monkeypatch):
    _sctx(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (proj / "tests").mkdir()
    monkeypatch.chdir(proj)
    out = slash.handle("/init", session="s", cfg=_cfg(), state={"yolo": False})
    assert "created SIDEKICK.md" in out.text
    body = (proj / "SIDEKICK.md").read_text()
    assert "Project: proj" in body and "Python" in body and "pytest" in body


def test_init_refuses_when_memory_exists(tmp_path, monkeypatch):
    _sctx(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "AGENTS.md").write_text("mine")
    monkeypatch.chdir(proj)
    out = slash.handle("/init", session="s", cfg=_cfg(), state={"yolo": False})
    assert out.text.startswith("Blocked: already have AGENTS.md")
    assert not (proj / "SIDEKICK.md").exists()


def test_init_never_clobbers(tmp_path, monkeypatch):
    _sctx(tmp_path, monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "SIDEKICK.md").write_text("hand-written")
    monkeypatch.chdir(proj)
    slash.handle("/init", session="s", cfg=_cfg(), state={"yolo": False})
    assert (proj / "SIDEKICK.md").read_text() == "hand-written"


def test_scaffold_unwritable_errors(tmp_path, monkeypatch):
    """Scaffold target that cannot be written surfaces as Error:, never raises."""
    _sctx(tmp_path, monkeypatch)
    blocker = tmp_path / "blocker"
    blocker.write_text("x")  # a file, so SIDEKICK.md cannot be created under it
    out = mf.scaffold_memory(str(blocker))
    assert out.startswith("Error: could not scaffold SIDEKICK.md")


def test_help_lists_init(tmp_path, monkeypatch):
    c = _sctx(tmp_path, monkeypatch)
    out = slash.handle("/help", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert "/init" in out.text
