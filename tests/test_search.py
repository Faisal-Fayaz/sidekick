"""Session search tests (fixes #190): FTS ranking + snippets, session
filter, namespace scoping, v4→v5 migration, sk search CLI. Fully offline."""

import sqlite3

import sk.store as store


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    monkeypatch.chdir(tmp_path)


def test_search_finds_across_sessions_with_snippets(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    store.save_message("chat-1", "user", "deploy runs on Fridays")
    store.save_message("chat-1", "assistant", "noted")
    store.save_message("chat-2", "user", "what about Friday deploys?")
    hits = store.search_sessions("friday deploy")
    assert len(hits) == 2
    sessions = {h["session"] for h in hits}
    assert sessions == {"chat-1", "chat-2"}
    assert all("»" in h["snippet"] and h["ts"] > 0 for h in hits)
    assert hits[0]["session"] == "chat-2"  # newest first


def test_search_session_filter_and_empty(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    store.save_message("a", "user", "unique needle here")
    store.save_message("b", "user", "unique needle there")
    assert [h["session"] for h in store.search_sessions("needle", session="a")] == ["a"]
    assert store.search_sessions("") == []
    assert store.search_sessions("zzz-no-such-term") == []


def test_search_respects_namespaces(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    store.set_default_namespace("proj")
    store.save_message("p", "user", "project needle alpha")
    store.set_default_namespace("")
    store.save_message("g", "user", "global needle beta")
    try:
        g_hits = store.search_sessions("needle")
        assert [h["session"] for h in g_hits] == ["g"]
        p_hits = store.search_sessions("needle", namespace="proj")
        sessions = {h["session"] for h in p_hits}
        assert sessions == {"p", "g"}
    finally:
        store.set_default_namespace("")


def test_migrate_4_to_5_backfills_fts(tmp_path, monkeypatch):
    import sk.config as config_mod

    db = tmp_path / "history.db"
    monkeypatch.setattr(store, "DB_PATH", db)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " session TEXT, role TEXT, content TEXT, ts REAL)"
    )
    conn.execute(
        "INSERT INTO messages (session, role, content, ts) VALUES ('old', 'user', 'legacy needle', 1.0)"
    )
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()
    hits = store.search_sessions("legacy needle")
    assert [h["session"] for h in hits] == ["old"]
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
    assert "namespace" in cols
    conn.close()


def test_sk_search_cli(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    from typer.testing import CliRunner

    from sk.cli import app

    store.save_message("chat-9", "assistant", "the deploy checklist lives here")
    res = CliRunner().invoke(app, ["search", "deploy checklist"])
    assert res.exit_code == 0, res.output
    assert "chat-9" in res.output and "checklist" in res.output
    res = CliRunner().invoke(app, ["search", "deploy checklist", "--session", "other"])
    assert "no matching" in res.output
    res = CliRunner().invoke(app, ["search", "zzz-no-such-term"])
    assert "no matching" in res.output
