"""Richer memory: decay, dedup and proposal-only extraction (#493)."""

from __future__ import annotations

import sqlite3
import time

import pytest
from typer.testing import CliRunner

import sk.memory_rank as rank
import sk.store as store
from sk.cli import app


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


# --- pure helpers ----------------------------------------------------------


def test_normalise_and_similarity_ignore_punctuation_and_case():
    assert rank.normalise("  I prefer Qwen, 3:4b! ") == "i prefer qwen 3 4b"
    assert rank.similarity("I prefer qwen 3:4b", "i prefer qwen 3 4b!") == 1.0
    assert rank.similarity("unrelated text", "a totally different thing") < 0.5


def test_recency_decays_and_importance_grows():
    now = 1_000_000.0
    assert rank.recency_score(now, now=now) == 1.0
    assert rank.recency_score(now - 30 * 86400, now=now) == pytest.approx(0.5)
    assert rank.importance_score(0) == 1.0
    assert rank.importance_score(3) > rank.importance_score(1)
    # A future stamp must not boost a fact.
    assert rank.recency_score(now + 86400, now=now) == 1.0


def test_rank_combines_all_three_signals():
    now = 1_000_000.0
    fresh_used = rank.rank_score(0.8, now - 100, uses=5, now=now)
    fresh_new = rank.rank_score(0.8, now - 100, uses=0, now=now)
    stale_used = rank.rank_score(0.8, now - 60 * 86400, uses=5, now=now)
    assert fresh_used > fresh_new > stale_used


def test_proposals_are_narrow_and_never_write(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")

    def no_database(*a, **kw):
        raise AssertionError("extracting a proposal touched the database")

    monkeypatch.setattr(store, "_connect", no_database)
    facts = rank.proposals(
        "Hi there.\nRemember that deploys happen on Fridays.\nMy staging URL is staging.internal.\n"
        "I prefer small pull requests.\nWhat is the weather?"
    )
    assert facts == [
        "deploys happen on Fridays",
        "staging URL is staging.internal",
        "small pull requests",
    ]
    assert rank.proposals("the sky is blue") == []


# --- settings --------------------------------------------------------------


def test_settings_from_global_file_and_env(tmp_path, monkeypatch):
    from sk import config as config_mod

    cfg = tmp_path / "config.toml"
    cfg.write_text("memory_half_life_days = 7\nmemory_dedup_threshold = 0.9\n")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg)
    monkeypatch.delenv("SIDEKICK_MEMORY_HALF_LIFE_DAYS", raising=False)
    monkeypatch.delenv("SIDEKICK_MEMORY_DEDUP_THRESHOLD", raising=False)
    assert rank.settings() == {"half_life_days": 7.0, "dedup_threshold": 0.9}

    monkeypatch.setenv("SIDEKICK_MEMORY_HALF_LIFE_DAYS", "14")
    assert rank.settings()["half_life_days"] == 14.0


def test_invalid_settings_fall_back(tmp_path, monkeypatch):
    from sk import config as config_mod

    cfg = tmp_path / "config.toml"
    cfg.write_text("memory_half_life_days = 'nope'\nmemory_dedup_threshold = 9.9\n")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg)
    for key in ("SIDEKICK_MEMORY_HALF_LIFE_DAYS", "SIDEKICK_MEMORY_DEDUP_THRESHOLD"):
        monkeypatch.delenv(key, raising=False)
    assert rank.settings() == {
        "half_life_days": rank.DEFAULT_HALF_LIFE_DAYS,
        "dedup_threshold": rank.DEFAULT_DEDUP_THRESHOLD,
    }


def test_a_project_file_cannot_set_memory_policy(tmp_path, monkeypatch):
    from sk import config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "missing.toml")
    (tmp_path / ".sidekick.toml").write_text(
        "memory_half_life_days = 1\nmemory_dedup_threshold = 0.5\n"
    )
    monkeypatch.chdir(tmp_path)
    for key in ("SIDEKICK_MEMORY_HALF_LIFE_DAYS", "SIDEKICK_MEMORY_DEDUP_THRESHOLD"):
        monkeypatch.delenv(key, raising=False)
    assert rank.settings()["half_life_days"] == rank.DEFAULT_HALF_LIFE_DAYS


# --- the store -------------------------------------------------------------


def test_near_duplicate_is_merged_not_stacked(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert "Remembered" in store.save_memory("I prefer qwen 3:4b for coding")
    assert "Merged" in store.save_memory("i prefer qwen 3 4b for coding!")
    conn = sqlite3.connect(tmp_path / "history.db")
    rows = conn.execute("SELECT content, uses FROM memories").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "i prefer qwen 3 4b for coding!"
    assert rows[0][1] >= 1


def test_distinct_facts_are_both_kept(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    store.save_memory("prefers qwen for coding")
    store.save_memory("deploys happen on Fridays")
    conn = sqlite3.connect(tmp_path / "history.db")
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 2
    conn.close()


def test_recall_ranks_fresh_used_fact_above_stale_one(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(rank, "settings", lambda: {"half_life_days": 1.0, "dedup_threshold": 0.85})
    store.save_memory("alpha preference for model qwen")  # will be made stale
    store.save_memory("beta preference for model qwen")  # stays fresh
    conn = sqlite3.connect(tmp_path / "history.db")
    now = time.time()
    conn.execute(
        "UPDATE memories SET ts=?, last_used=0, uses=0 WHERE content LIKE 'alpha%'",
        (now - 30 * 86400,),
    )
    conn.execute(
        "UPDATE memories SET ts=?, last_used=?, uses=5 WHERE content LIKE 'beta%'", (now, now)
    )
    conn.commit()
    conn.close()

    hits = store.recall_memories("preference model qwen", limit=2)
    assert hits[0].startswith("beta"), hits


def test_recall_still_works_without_an_fts_index(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    store.save_memory("a very particular memory about oranges")
    conn = sqlite3.connect(tmp_path / "history.db")
    conn.execute("DROP TABLE memories_fts")
    conn.commit()
    conn.close()
    assert store.recall_memories("oranges") == ["a very particular memory about oranges"]


def test_memory_proposals_cli(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    result = CliRunner().invoke(app, ["memory-proposals", "Remember that deploys are on Fridays."])
    assert result.exit_code == 0, result.output
    assert "deploys are on Fridays" in result.output
    assert "nothing was saved" in result.output
    # Not even a database file: extracting a proposal never opens the store.
    assert not (tmp_path / "history.db").exists()
