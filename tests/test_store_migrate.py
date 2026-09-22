"""Store migration tests: PRAGMA user_version stamping, no data loss."""

import sqlite3

import sk.store as store


def _version(db_path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def test_fresh_db_stamped_current(tmp_path, monkeypatch):
    db = tmp_path / "history.db"
    monkeypatch.setattr(store, "DB_PATH", db)
    conn = store._connect()
    conn.close()
    assert _version(db) == store.SCHEMA_VERSION
    assert store.SCHEMA_VERSION == 2


def test_preversioning_db_upgrades_preserving_data(tmp_path, monkeypatch):
    db = tmp_path / "history.db"
    # simulate a pre-versioning DB: tables exist, user_version == 0, with data
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, role TEXT, content TEXT, ts REAL)"
    )
    conn.execute(
        "CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT UNIQUE, ts REAL)"
    )
    conn.execute(
        "CREATE TABLE todos (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, done INTEGER DEFAULT 0, ts REAL)"
    )
    conn.execute(
        "CREATE TABLE shell_history (id INTEGER PRIMARY KEY AUTOINCREMENT, cmd TEXT, cwd TEXT DEFAULT '', exit INTEGER DEFAULT 0, ts REAL)"
    )
    conn.execute("INSERT INTO memories (content, ts) VALUES ('prefers fast', 1.0)")
    conn.commit()
    conn.close()
    assert _version(db) == 0

    monkeypatch.setattr(store, "DB_PATH", db)
    store.save_message("s", "user", "hi")
    assert store.recall_memories("fast") == ["prefers fast"]
    assert _version(db) == store.SCHEMA_VERSION


def test_v1_to_v2_adds_tool_runs_preserving_data(tmp_path, monkeypatch):
    db = tmp_path / "history.db"
    monkeypatch.setattr(store, "DB_PATH", db)
    conn = store._connect()
    conn.close()
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version = 1")
    conn.execute("DROP TABLE tool_runs")
    conn.execute("INSERT INTO memories (content, ts) VALUES ('prefers fast', 1.0)")
    conn.commit()
    conn.close()

    store.log_tool_run("s", "list_dir", "list_dir|path=/tmp", True, "ollama", "localhost", True)
    assert store.recall_memories("fast") == ["prefers fast"]
    rows = store.list_tool_runs("s")
    assert len(rows) == 1 and rows[0]["tool"] == "list_dir"
    assert _version(db) == 2


def test_newer_db_left_untouched(tmp_path, monkeypatch):
    db = tmp_path / "history.db"
    monkeypatch.setattr(store, "DB_PATH", db)
    conn = store._connect()
    conn.close()
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()

    conn = store._connect()
    conn.close()
    assert _version(db) == 99
