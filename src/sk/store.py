"""SQLite history store. Keeps chat + run logs in ~/.sidekick/history.db"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DB_PATH = Path.home() / ".sidekick" / "history.db"

# Schema version, stamped via PRAGMA user_version. Bump when adding tables or
# columns; add a _migrate_N_to_N+1() and wire it in _migrate().
# v1 = base tables (messages/memories/todos/shell_history + memories_fts).
# v2 = tool_runs audit log (session, tool, target, approved, provider, host).
# v3 = memories.namespace for per-project scoping (Config.load publishes it).
# v4 = session_summaries rolling compaction watermarks.
SCHEMA_VERSION = 4

# Process-wide memory namespace, published by Config.load() from the active
# project file (or "" outside projects). Callers may pass an explicit
# namespace instead; None means "use this default".
_default_namespace: str = ""


def set_default_namespace(ns: str) -> None:
    """Set the process memory namespace (called by Config.load). Test-safe."""
    global _default_namespace
    _default_namespace = (ns or "").strip()


def _resolve_namespace(namespace: str | None) -> str:
    return _default_namespace if namespace is None else (namespace or "").strip()


def _ns_clause(namespace: str, alias: str = "") -> tuple[str, tuple[str, ...]]:
    """Visibility: global namespace sees only global; a project sees global + its own.

    alias must match the query's table alias ("" for unaliased FROM memories).
    """
    col = f"{alias}.namespace" if alias else "namespace"
    if namespace:
        return (f"({col} = '' OR {col} = ?)", (namespace,))
    return (f"{col} = ''", ())


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
    if v < 2:
        _migrate_1_to_2(conn)
        v = 2
    if v < 3:
        _migrate_2_to_3(conn)
        v = 3
    if v < 4:
        _migrate_3_to_4(conn)
        v = 4
    _set_version(conn, v)
    conn.commit()
    return v


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    """v2: tool_runs audit log. Idempotent; existing rows untouched."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tool_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session TEXT NOT NULL DEFAULT '',
            tool TEXT NOT NULL,
            target TEXT NOT NULL DEFAULT '',
            approved INTEGER NOT NULL DEFAULT 1,
            provider TEXT NOT NULL DEFAULT '',
            host TEXT NOT NULL DEFAULT '',
            ok INTEGER NOT NULL DEFAULT 1,
            ts REAL NOT NULL
        )"""
    )


def _migrate_2_to_3(conn: sqlite3.Connection) -> None:
    """v3: memories.namespace for per-project scoping. Existing rows stay global."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()]
    if "namespace" not in cols:
        conn.execute("ALTER TABLE memories ADD COLUMN namespace TEXT NOT NULL DEFAULT ''")


def _migrate_3_to_4(conn: sqlite3.Connection) -> None:
    """v4: session_summaries rolling compaction watermarks. Idempotent."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS session_summaries (
            session TEXT PRIMARY KEY,
            summary TEXT NOT NULL DEFAULT '',
            up_to_id INTEGER NOT NULL DEFAULT 0,
            ts REAL NOT NULL
        )"""
    )


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
    """Fresh session id. Microsecond resolution: two forks/clears within the
    same second must never share an id (that silently merges histories)."""
    import time as _t

    stamp = _t.strftime("%Y%m%d-%H%M%S") + f"{_t.time_ns() % 1_000_000:06d}"
    return f"{prefix}-{stamp}"


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


def fork_session(session: str, keep_n: int | None, new_session: str) -> tuple[bool, str]:
    """Copy the first keep_n messages (None = all) into new_session. Returns (ok, msg).

    Roles, contents and timestamps copied verbatim (fresh ids). Audit rows
    and compaction summaries are deliberately NOT copied: the fork earns its
    own going forward. Never raises; errors return (False, reason).
    """
    if keep_n is not None:
        try:
            n = int(keep_n)
        except (TypeError, ValueError):
            return (False, "usage: `/fork [n]` (n = messages to keep)")
        if n <= 0:
            return (False, "usage: `/fork [n]` (n = messages to keep, starting at 1)")
    if not (new_session or "").strip():
        return (False, "new session id is empty.")
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT role, content, ts FROM messages WHERE session=? ORDER BY id ASC", (session,)
        )
        rows = cur.fetchall()
        if not rows:
            return (False, f"nothing to fork in '{session}'.")
        if keep_n is not None and n > len(rows):
            return (False, f"only {len(rows)} messages in '{session}' (asked for {n}).")
        subset = rows if keep_n is None else rows[:n]
        conn.executemany(
            "INSERT INTO messages (session, role, content, ts) VALUES (?, ?, ?, ?)",
            [(new_session, r, c, t) for r, c, t in subset],
        )
        conn.commit()
        return (True, f"forked {len(subset)} messages → `{new_session}`")
    except Exception as e:
        return (False, f"fork failed: {e}")
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


def get_history_full(session: str, after_id: int = 0, limit: int = 2000) -> list[dict]:
    """All messages with ids (for compaction watermarks). Oldest-first."""
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT id, role, content FROM messages WHERE session=? AND id > ?"
            " ORDER BY id ASC LIMIT ?",
            (session, after_id, limit),
        )
        return [{"id": i, "role": r, "content": c} for i, r, c in cur.fetchall()]
    finally:
        conn.close()


def get_summary(session: str) -> tuple[str, int]:
    """Rolling summary + watermark (up_to_id). ("", 0) when never compacted."""
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT summary, up_to_id FROM session_summaries WHERE session=?", (session,)
        )
        row = cur.fetchone()
        return (row[0], row[1]) if row else ("", 0)
    finally:
        conn.close()


def save_summary(session: str, summary: str, up_to_id: int) -> None:
    """Upsert rolling summary. Best-effort: never raises (compaction is optional)."""
    try:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO session_summaries (session, summary, up_to_id, ts)"
                " VALUES (?, ?, ?, ?) ON CONFLICT(session) DO UPDATE SET"
                " summary=excluded.summary, up_to_id=excluded.up_to_id, ts=excluded.ts",
                (session, summary, up_to_id, time.time()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def save_memory(content: str, namespace: str | None = None) -> str:
    content = content.strip()
    if not content:
        return "Empty, nothing saved."
    if len(content) > 2000:
        return "Too long (>2000 chars), keep memories short."
    ns = _resolve_namespace(namespace)
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO memories (content, namespace, ts) VALUES (?, ?, ?)",
            (content, ns, time.time()),
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


def recall_memories(query: str, limit: int = 5, namespace: str | None = None) -> list[str]:
    ns = _resolve_namespace(namespace)
    scope, params = _ns_clause(ns)
    conn = _connect()
    try:
        keys = _keywords(query)
        if not keys:
            cur = conn.execute(
                f"SELECT content FROM memories WHERE {scope} ORDER BY id DESC LIMIT ?",
                (*params, limit),
            )
            return [r[0] for r in cur.fetchall()]
        # 1) FTS5 ranked (prefix, typo-tolerant), joined for namespace scope
        try:
            fq = _fts_query(keys)
            if fq:
                fts_scope, fts_params = _ns_clause(ns, alias="m")
                cur = conn.execute(
                    "SELECT m.content FROM memories_fts f JOIN memories m ON m.id = f.rowid"
                    f" WHERE f.memories_fts MATCH ? AND {fts_scope} ORDER BY rank LIMIT ?",
                    (fq, *fts_params, limit),
                )
                rows = [r[0] for r in cur.fetchall()]
                if rows:
                    return rows
        except Exception:
            pass
        # 2) substring-score fallback (old behavior)
        cur = conn.execute(
            f"SELECT content FROM memories WHERE {scope} ORDER BY id DESC LIMIT 100", params
        )
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


def list_memories(limit: int = 50, namespace: str | None = None) -> list[str]:
    ns = _resolve_namespace(namespace)
    scope, params = _ns_clause(ns)
    conn = _connect()
    try:
        cur = conn.execute(
            f"SELECT content FROM memories WHERE {scope} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def forget_memory(query: str, namespace: str | None = None) -> str:
    ns = _resolve_namespace(namespace)
    scope, params = _ns_clause(ns)
    conn = _connect()
    try:
        cur = conn.execute(f"SELECT id, content FROM memories WHERE {scope}", params)
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


def log_tool_run(
    session: str,
    tool: str,
    target: str = "",
    approved: bool = True,
    provider: str = "",
    host: str = "",
    ok: bool = True,
) -> None:
    """Append one audit row. Best-effort: never raises (audit must not break runs)."""
    try:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO tool_runs (session, tool, target, approved, provider, host, ok, ts)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session or "",
                    tool,
                    (target or "")[:500],
                    1 if approved else 0,
                    provider or "",
                    host or "",
                    1 if ok else 0,
                    time.time(),
                ),
            )
            conn.commit()
            conn.execute(
                "DELETE FROM tool_runs WHERE id NOT IN"
                " (SELECT id FROM tool_runs ORDER BY id DESC LIMIT 5000)"
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def list_tool_runs(session: str = "", limit: int = 200) -> list[dict]:
    """Audit rows, newest first. Empty session = all sessions."""
    conn = _connect()
    try:
        if session:
            cur = conn.execute(
                "SELECT session, tool, target, approved, provider, host, ok, ts"
                " FROM tool_runs WHERE session=? ORDER BY id DESC LIMIT ?",
                (session, limit),
            )
        else:
            cur = conn.execute(
                "SELECT session, tool, target, approved, provider, host, ok, ts"
                " FROM tool_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        keys = ("session", "tool", "target", "approved", "provider", "host", "ok", "ts")
        return [dict(zip(keys, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def is_local_traffic(provider: str, host: str) -> bool:
    """True if the traffic stayed on this machine. Pure string checks, no DNS."""
    if (provider or "").strip().lower() in ("ollama", "lmstudio"):
        return True
    h = (host or "").strip().lower().split(":")[0]
    if h in ("localhost", "127.0.0.1", "::1", ""):
        return True
    if h.startswith(("192.168.", "10.")):
        return True
    if h.startswith("172."):
        try:
            second = int(h.split(".")[1])
            if 16 <= second <= 31:
                return True
        except (ValueError, IndexError):
            pass
    return False


def export_session(session: str) -> list[dict]:
    """Full event stream for one session, oldest-first. [] if unknown/empty.

    Merges chat messages (role/content/ts) with audit tool runs
    (tool/target/approved/provider/host/ts), sorted by timestamp.
    Pure reads; no network.
    """
    if not (session or "").strip():
        return []
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT role, content, ts FROM messages WHERE session=? ORDER BY id ASC",
            (session,),
        )
        events = [
            {"kind": "msg", "role": r, "content": c, "ts": t or 0} for r, c, t in cur.fetchall()
        ]
        cur = conn.execute(
            "SELECT tool, target, approved, provider, host, ok, ts"
            " FROM tool_runs WHERE session=? ORDER BY id ASC",
            (session,),
        )
        for tool, target, approved, provider, host, ok, ts in cur.fetchall():
            events.append(
                {
                    "kind": "tool",
                    "tool": tool,
                    "target": target,
                    "approved": approved,
                    "provider": provider,
                    "host": host,
                    "ok": ok,
                    "ts": ts or 0,
                }
            )
        events.sort(key=lambda e: e["ts"])
        return events
    finally:
        conn.close()


def render_transcript(session: str, events: list[dict]) -> str:
    """Render an export_session() stream as portable Markdown."""
    import datetime as _dt

    turns = sum(1 for e in events if e["kind"] == "msg" and e["role"] == "user")
    tools = sum(1 for e in events if e["kind"] == "tool")
    exported = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Session `{session}` — {turns} turns, {tools} tool calls",
        f"_Exported {exported}_",
        "",
    ]
    for e in events:
        ts = _dt.datetime.fromtimestamp(e["ts"]).strftime("%m-%d %H:%M") if e["ts"] else "--"
        if e["kind"] == "msg":
            lines.append(f"## [{ts}] {e['role']}")
            lines.append(e["content"] or "(empty)")
        else:
            if not e["approved"]:
                mark = "DENIED"
            elif not e["ok"]:
                mark = "✗ failed"
            else:
                mark = "✓"
            where = (
                "local"
                if is_local_traffic(e["provider"], e["host"])
                else f"{e['provider']}@{e['host']}"
            )
            lines.append(f"## [{ts}] tool `{e['tool']}` {mark} ({where})")
            lines.append(f"`{e['target'][:200]}`" if e["target"] else "(no target)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# Approximate input $/MTok for known models. Estimates only: real bills
# depend on input/output mix and current pricing. Unknown models → n/a.
COST_PER_MTOK: dict[str, float] = {
    "claude-sonnet-5": 2.0,
    "claude-opus-5": 5.0,
    "claude-haiku-4-5": 1.0,
}


def _est_tokens(text: str) -> int:
    """chars/4 heuristic for usage accounting (matches agent.estimate_tokens)."""
    return max(0, len(text or "") // 4)


def usage_stats(session: str = "", limit: int = 5000) -> dict:
    """Aggregate usage from audit rows + message contents. No new storage.

    Tokens are chars/4 estimates; costs apply known input rates only and are
    marked approximate. Session tokens attribute to that session's most-used
    llm_call model (exact for the normal single-model case).
    """
    runs = list_tool_runs(session.strip(), limit=max(1, min(limit, 10000)))
    conn = _connect()
    try:
        if session.strip():
            cur = conn.execute(
                "SELECT session, role, content FROM messages WHERE session=? ORDER BY id ASC LIMIT ?",
                (session.strip(), limit),
            )
        else:
            cur = conn.execute(
                "SELECT session, role, content FROM messages ORDER BY id ASC LIMIT ?", (limit,)
            )
        messages = [{"session": s, "role": r, "content": c} for s, r, c in cur.fetchall()]
    finally:
        conn.close()

    by_session: dict[str, dict] = {}
    for m in messages:
        by_session.setdefault(m["session"], {"tokens": 0, "model": ""})
        by_session[m["session"]]["tokens"] += _est_tokens(m["content"])
    model_counts: dict[str, dict[str, int]] = {}
    for r in runs:
        if r["tool"] == "llm_call":
            model = (r["target"] or "").strip() or "?"
            by_session.setdefault(r["session"], {"tokens": 0, "model": ""})
            model_counts.setdefault(r["session"], {}).setdefault(model, 0)
            model_counts[r["session"]][model] += 1
    for s, counts in model_counts.items():
        by_session.setdefault(s, {"tokens": 0, "model": ""})
        by_session[s]["model"] = max(counts, key=lambda m: counts[m])
    for r in runs:
        s = r["session"]
        by_session.setdefault(s, {"tokens": 0, "model": ""})
        by_session[s]["tokens"] += _est_tokens(r["target"])

    tool_counts: dict[str, int] = {}
    denied = failed = local = egress = 0
    for r in runs:
        if r["tool"] != "llm_call":
            tool_counts[r["tool"]] = tool_counts.get(r["tool"], 0) + 1
        if not r["approved"]:
            denied += 1
        elif not r["ok"]:
            failed += 1
        if is_local_traffic(r["provider"], r["host"]):
            local += 1
        else:
            egress += 1

    per_model: dict[str, dict] = {}
    cost_known = 0.0
    unknown_tokens = 0
    for s, info in by_session.items():
        model = info["model"] or "?"
        entry = per_model.setdefault(model, {"turns": 0, "tokens": 0, "cost_usd": None})
        entry["tokens"] += info["tokens"]
        rate = COST_PER_MTOK.get(model)
        if rate is not None:
            charged = round(info["tokens"] / 1_000_000 * rate, 4)
            entry["cost_usd"] = round((entry["cost_usd"] or 0.0) + charged, 4)
            cost_known = round(cost_known + charged, 4)
        else:
            unknown_tokens += info["tokens"]
    for s, counts in model_counts.items():
        per_model.setdefault("?", {"turns": 0, "tokens": 0, "cost_usd": None})
        per_model[by_session[s]["model"] or "?"]["turns"] += sum(counts.values())

    return {
        "turns": sum(1 for r in runs if r["tool"] == "llm_call"),
        "tool_runs": len([r for r in runs if r["tool"] != "llm_call"]),
        "tools": tool_counts,
        "denied": denied,
        "failed": failed,
        "tokens": sum(v["tokens"] for v in by_session.values()),
        "local_runs": local,
        "egress_runs": egress,
        "sessions": len(by_session),
        "per_model": per_model,
        "cost_usd": round(cost_known, 4) if cost_known else None,
        "unpriced_tokens": unknown_tokens,
    }
