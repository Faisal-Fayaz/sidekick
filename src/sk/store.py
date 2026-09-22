"""SQLite history store. Keeps chat + run logs in ~/.sidekick/history.db"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DB_PATH = Path.home() / ".sidekick" / "history.db"

# Schema version, stamped via PRAGMA user_version. Bump when adding tables or
# columns; add a _migrate_N_to_N+1() and wire it in _migrate(). v1 = current
# tables (messages/memories/todos/shell_history + memories_fts).
SCHEMA_VERSION = 1


def _get_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _set_version(conn: sqlite3.Connection, v: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(v)}")


def _migrate_0_to_1(conn: sqlite3.Connection) -> None:
    """Fresh DB (or pre-versioning DB): create current tables. Idempotent."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            ts REAL NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL UNIQUE,
            ts REAL NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            ts REAL NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS shell_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cmd TEXT NOT NULL,
            cwd TEXT NOT NULL DEFAULT '',
            exit INTEGER NOT NULL DEFAULT 0,
            ts REAL NOT NULL
        )"""
    )
    # FTS5 for semantic-ish recall (zero deps, stdlib). Best-effort.
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(content)")
        # backfill any rows missing from fts (first run after upgrade)
        conn.execute(
            "INSERT INTO memories_fts(rowid, content) SELECT id, content FROM memories WHERE id NOT IN (SELECT rowid FROM memories_fts)"
        )
        conn.commit()
    except Exception:
        pass


def _migrate(conn: sqlite3.Connection) -> int:
    """Bring conn up to SCHEMA_VERSION. Returns final version. Never drops data."""
    v = _get_version(conn)
    if v > SCHEMA_VERSION:
        # DB from a newer sidekick; leave untouched (forward-compat).
        return v
    if v < 1:
        _migrate_0_to_1(conn)
        v = 1
    # future: if v < 2: _migrate_1_to_2(conn); v = 2
    _set_version(conn, v)
    conn.commit()
    return v


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    _migrate(conn)
    conn.commit()
    return conn


def clear_session(session: str) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM messages WHERE session=?", (session,))
        conn.commit()
    finally:
        conn.close()


def new_session_id(prefix: str = "tui") -> str:
    import time as _t

    return f"{prefix}-{_t.strftime('%Y%m%d-%H%M%S')}"


def list_sessions(limit: int = 20) -> list[dict]:
    """Recent chat sessions: id, messages, last-ts, preview (first user line)."""
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT session, COUNT(*), MAX(ts) FROM messages GROUP BY session ORDER BY MAX(ts) DESC LIMIT ?",
            (limit,),
        )
        out = []
        for session, count, last_ts in cur.fetchall():
            cur2 = conn.execute(
                "SELECT content FROM messages WHERE session=? AND role='user' ORDER BY id ASC LIMIT 1",
                (session,),
            )
            row = cur2.fetchone()
            preview = (row[0] if row else "")[:80].replace("\n", " ")
            out.append(
                {"session": session, "count": count, "last_ts": last_ts or 0, "preview": preview}
            )
        return out
    finally:
        conn.close()


def delete_session(session: str) -> int:
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM messages WHERE session=?", (session,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def latest_session(prefix: str = "") -> str:
    """Most recently active session id, optionally filtered by prefix."""
    conn = _connect()
    try:
        if prefix:
            cur = conn.execute(
                "SELECT session FROM messages WHERE session LIKE ? GROUP BY session ORDER BY MAX(ts) DESC LIMIT 1",
                (prefix + "%",),
            )
        else:
            cur = conn.execute(
                "SELECT session FROM messages GROUP BY session ORDER BY MAX(ts) DESC LIMIT 1"
            )
        row = cur.fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


def save_message(session: str, role: str, content: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO messages (session, role, content, ts) VALUES (?, ?, ?, ?)",
            (session, role, content, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def get_history(session: str, limit: int = 20) -> list[dict]:
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT role, content FROM messages WHERE session=? ORDER BY id DESC LIMIT ?",
            (session, limit),
        )
        rows = cur.fetchall()
        # return oldest-first
        return [{"role": r, "content": c} for r, c in reversed(rows)]
    finally:
        conn.close()


def save_memory(content: str) -> str:
    content = content.strip()
    if not content:
        return "Empty, nothing saved."
    if len(content) > 2000:
        return "Too long (>2000 chars), keep memories short."
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO memories (content, ts) VALUES (?, ?)", (content, time.time())
        )
        if cur.rowcount:
            try:
                conn.execute(
                    "INSERT INTO memories_fts(rowid, content) VALUES (last_insert_rowid(), ?)",
                    (content,),
                )
            except Exception:
                pass
        conn.commit()
        cur = conn.execute("SELECT COUNT(*) FROM memories")
        n = cur.fetchone()[0]
        return f"Remembered ({n} total)."
    finally:
        conn.close()


def _keywords(query: str) -> list[str]:
    import re

    words = re.findall(r"[a-z0-9]+", query.lower())
    stop = {
        "the",
        "a",
        "an",
        "my",
        "me",
        "i",
        "you",
        "and",
        "or",
        "what",
        "how",
        "is",
        "are",
        "do",
        "does",
        "can",
        "tell",
        "give",
        "show",
        "please",
    }
    return [w for w in words if len(w) > 2 and w not in stop][:8]


def _fts_query(keys: list[str]) -> str:
    # prefix match each token: "prefers*" AND "fast*" — handles plurals/typos better than LIKE
    import re

    toks = [re.sub(r"[^a-z0-9]", "", k.lower()) for k in keys]
    toks = [t for t in toks if len(t) > 2][:8]
    return " AND ".join(f'"{t}"*' for t in toks)


def recall_memories(query: str, limit: int = 5) -> list[str]:
    conn = _connect()
    try:
        keys = _keywords(query)
        if not keys:
            cur = conn.execute("SELECT content FROM memories ORDER BY id DESC LIMIT ?", (limit,))
            return [r[0] for r in cur.fetchall()]
        # 1) FTS5 ranked (prefix, typo-tolerant)
        try:
            fq = _fts_query(keys)
            if fq:
                cur = conn.execute(
                    "SELECT content FROM memories_fts WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
                    (fq, limit),
                )
                rows = [r[0] for r in cur.fetchall()]
                if rows:
                    return rows
        except Exception:
            pass
        # 2) substring-score fallback (old behavior)
        cur = conn.execute("SELECT content FROM memories ORDER BY id DESC LIMIT 100")
        rows = [r[0] for r in cur.fetchall()]
        scored: list[tuple[int, str]] = []
        for c in rows:
            cl = c.lower()
            s = sum(1 for k in keys if k in cl)
            if s > 0:
                scored.append((s, c))
        scored.sort(reverse=True)
        if scored:
            return [c for _, c in scored[:limit]]
        # fallback: recent
        return rows[:limit]
    finally:
        conn.close()


def list_memories(limit: int = 50) -> list[str]:
    conn = _connect()
    try:
        cur = conn.execute("SELECT content FROM memories ORDER BY id DESC LIMIT ?", (limit,))
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def forget_memory(query: str) -> str:
    conn = _connect()
    try:
        cur = conn.execute("SELECT id, content FROM memories")
        rows = cur.fetchall()
        ql = query.lower()
        killed = 0
        for i, c in rows:
            if ql in c.lower() or c.lower() in ql:
                conn.execute("DELETE FROM memories WHERE id=?", (i,))
                try:
                    conn.execute("DELETE FROM memories_fts WHERE rowid=?", (i,))
                except Exception:
                    pass
                killed += 1
        conn.commit()
        return f"Forgot {killed}."
    finally:
        conn.close()


def add_todo(text: str) -> str:
    text = text.strip()
    if not text:
        return "Empty, nothing added."
    if len(text) > 500:
        return "Too long (>500 chars)."
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO todos (text, done, ts) VALUES (?, 0, ?)", (text, time.time())
        )
        conn.commit()
        return f"Added todo #{cur.lastrowid}."
    finally:
        conn.close()


def list_todos(open_only: bool = True) -> list[tuple[int, str, int]]:
    conn = _connect()
    try:
        if open_only:
            cur = conn.execute("SELECT id, text, done FROM todos WHERE done=0 ORDER BY id")
        else:
            cur = conn.execute("SELECT id, text, done FROM todos ORDER BY id")
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]
    finally:
        conn.close()


def complete_todo(tid: int) -> str:
    conn = _connect()
    try:
        cur = conn.execute("UPDATE todos SET done=1 WHERE id=? AND done=0", (tid,))
        conn.commit()
        return f"Done #{tid}." if cur.rowcount else f"Todo #{tid} not found/open."
    finally:
        conn.close()


def clear_todos() -> str:
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM todos WHERE done=1")
        conn.commit()
        return f"Cleared {cur.rowcount} done."
    finally:
        conn.close()


SKIP_PREFIXES = ("sk hook-log", "sk hook_log")


def log_shell(cmd: str, cwd: str = "", exit: int = 0) -> bool:
    cmd = (cmd or "").strip()[:2000]
    if not cmd:
        return False
    # skip our own hook noise + secrets
    if cmd.startswith(SKIP_PREFIXES):
        return False
    if "sk " in cmd and "hook-log" in cmd:
        return False
    conn = _connect()
    try:
        # skip exact consecutive dupes
        cur = conn.execute("SELECT cmd FROM shell_history ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if row and row[0] == cmd:
            return False
        conn.execute(
            "INSERT INTO shell_history (cmd, cwd, exit, ts) VALUES (?, ?, ?, ?)",
            (cmd, cwd[:500], int(exit or 0), time.time()),
        )
        conn.commit()
        # cap 2000 rows
        conn.execute(
            "DELETE FROM shell_history WHERE id NOT IN (SELECT id FROM shell_history ORDER BY id DESC LIMIT 2000)"
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_shell(limit: int = 20) -> list[tuple[int, str, str, int]]:
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT id, cmd, cwd, exit FROM shell_history ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [(r[0], r[1], r[2], r[3]) for r in cur.fetchall()]
    finally:
        conn.close()


def last_failed() -> tuple[int, str, str, int] | None:
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT id, cmd, cwd, exit FROM shell_history WHERE exit != 0 ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        return (row[0], row[1], row[2], row[3]) if row else None
    finally:
        conn.close()
