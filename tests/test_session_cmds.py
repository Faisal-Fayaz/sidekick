"""Session slash parity tests: /compact, /diff, /review (fixes #100). Offline
(git-binary tests skip when git is missing)."""

import shutil
import subprocess

import pytest

import sk.agent as agent
import sk.slash as slash
import sk.store as store
from sk.config import Config
from sk.gitdiff import git_diff_text

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


def _git(cwd, *args):
    r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=30, cwd=str(cwd))
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("needs git")
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t.t")
    _git(d, "config", "user.name", "t")
    (d / "a.txt").write_text("one\n")
    _git(d, "add", "a.txt")
    _git(d, "commit", "-qm", "init")
    return d


@needs_git
def test_diff_shows_stat_and_patch(repo):
    (repo / "a.txt").write_text("one\ntwo\n")
    out = git_diff_text(str(repo))
    assert "**diff vs HEAD**" in out
    assert "a.txt" in out
    assert "```diff" in out and "+two" in out


@needs_git
def test_diff_clean_tree(repo):
    assert git_diff_text(str(repo)) == "_(no changes vs HEAD)_"


@needs_git
def test_diff_bad_base(repo):
    (repo / "a.txt").write_text("x\n")
    out = git_diff_text(str(repo), base="no-such-ref")
    assert "could not diff vs no-such-ref" in out


def test_diff_not_a_repo(tmp_path):
    out = git_diff_text(str(tmp_path))
    assert out.startswith("_(not a git repo:")


@needs_git
def test_diff_fresh_repo_no_commits(tmp_path):
    d = tmp_path / "fresh"
    d.mkdir()
    _git(d, "init", "-q")
    (d / "new.txt").write_text("hi\n")
    out = git_diff_text(str(d))
    assert "no commits yet" in out


@needs_git
def test_diff_truncates(repo):
    (repo / "a.txt").write_text("x\n" * 5000)
    out = git_diff_text(str(repo), cap=100)
    assert "[diff truncated at 100 chars]" in out


@needs_git
def test_diff_counts_untracked(repo):
    (repo / "a.txt").write_text("two\n")
    (repo / "b.txt").write_text("new\n")
    out = git_diff_text(str(repo))
    assert "1 untracked files not shown" in out


@needs_git
def test_slash_diff_and_review(repo, tmp_path, monkeypatch):
    c = _sctx(tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    (repo / "a.txt").write_text("one\ntwo\n")
    out = slash.handle("/diff", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled and "+two" in out.text
    out = slash.handle("/review", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled and out.agent_prompt
    assert "severity" in out.agent_prompt and "+two" in out.agent_prompt


@needs_git
def test_slash_review_clean_tree(repo, tmp_path, monkeypatch):
    c = _sctx(tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    out = slash.handle("/review", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled and not out.agent_prompt
    assert "no changes" in out.text


def test_slash_compact_wiring(tmp_path, monkeypatch):
    c = _sctx(tmp_path, monkeypatch)
    monkeypatch.setattr(
        agent, "compact_session_now", lambda s, cfg, hint="": f"compacted! hint={hint!r}"
    )
    out = slash.handle("/compact", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.handled and out.text == "compacted! hint=''"
    out = slash.handle("/compact focus on auth", session="s", cfg=c["cfg"], state=c["state"])
    assert "focus on auth" in out.text


def test_help_lists_new_commands(tmp_path, monkeypatch):
    c = _sctx(tmp_path, monkeypatch)
    out = slash.handle("/help", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert "/compact" in out.text and "/diff" in out.text and "/review" in out.text


def _seed(session, n, size=50):
    for i in range(n):
        store.save_message(session, "user" if i % 2 == 0 else "assistant", f"m{i} " + "x" * size)


def test_compact_now_folds_and_saves(tmp_path, monkeypatch):
    _sctx(tmp_path, monkeypatch)
    monkeypatch.setattr(agent, "make_summarizer", lambda cfg: lambda text: "BIG PICTURE")
    cfg = _cfg(history_budget_tokens=200)
    _seed("s", 30, size=100)
    report = agent.compact_session_now("s", cfg)
    assert "compacted 30 messages" in report
    prior, _ = store.get_summary("s")
    assert prior == "BIG PICTURE"
    # second call: everything covered
    assert "already compacted" in agent.compact_session_now("s", cfg)


def test_compact_now_empty_and_under_budget(tmp_path, monkeypatch):
    _sctx(tmp_path, monkeypatch)
    cfg = _cfg(history_budget_tokens=3000)
    assert "empty" in agent.compact_session_now("nope", cfg)
    _seed("s", 2, size=10)
    assert "under budget" in agent.compact_session_now("s", cfg)


def test_compact_now_failure_reports(tmp_path, monkeypatch):
    _sctx(tmp_path, monkeypatch)

    def boom(cfg):
        raise RuntimeError("no provider")

    monkeypatch.setattr(agent, "make_summarizer", boom)
    cfg = _cfg(history_budget_tokens=200)
    _seed("s", 30, size=100)
    report = agent.compact_session_now("s", cfg)
    assert "history untouched" in report
    assert store.get_history("s")  # DB history intact
