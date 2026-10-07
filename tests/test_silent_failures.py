"""Best-effort writes that failed silently (#370).

Every one of these is a place where "never raises" was implemented as
`except Exception: pass`, and where the swallowed failure meant the tool
reported success while the thing it promised did not happen:

- a PreToolUse hook that raised was *skipped*, and an exception mid-iteration
  discarded denies already collected, so the tool ran;
- a checkpoint that did not save left `/rewind` restoring stale state while
  saying it worked, and took the redo stack with it;
- a failed egress ledger write removed the only evidence that anything was ever
  blocked, which is what `sk audit --prove` rests on;
- a row that failed to index was still stored, so search silently missed it.

The tests force the failure and assert it is reported, because the whole point
is that the report exists.
"""

from __future__ import annotations

import pytest

import sk.checkpoints as cp
import sk.egress as eg
import sk.hooks as hooks
import sk.store as store


@pytest.fixture(autouse=True)
def _clear_warn_dedup():
    """The warnings are once-per-process; tests need a clean slate."""
    cp._warned.clear()
    eg._ledger_warned.clear()
    store._warned.clear()
    yield
    cp._warned.clear()
    eg._ledger_warned.clear()
    store._warned.clear()


def _boom(monkeypatch, target, attr="atomic_write_text"):
    def _raise(*a, **kw):
        raise OSError("no space left on device")

    monkeypatch.setattr(target, attr, _raise)


# --- hooks: a security gate must fail closed -------------------------------


def test_hook_that_raises_denies_rather_than_being_skipped(monkeypatch):
    """A PreToolUse handler that cannot be consulted has not approved anything.

    Skipping it meant the tool ran. `run_hook` is documented never to raise, so
    reaching this means our own code broke -- and unexpected must fail closed.
    """
    monkeypatch.setattr(
        hooks, "load_hooks", lambda: [{"event": "PreToolUse", "command": "whatever"}]
    )
    monkeypatch.setattr(
        hooks, "run_hook", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    results = hooks.fire_event("PreToolUse", {"session": "s", "tool": "write_file", "args": {}})
    assert [r["decision"] for r in results] == ["deny"], results
    assert "boom" in results[0]["reason"]


def test_post_tool_use_hook_that_raises_is_still_best_effort(monkeypatch):
    """A notification that did not fire must not block a turn.

    The asymmetry with PreToolUse is deliberate: one gates a tool call, the
    other observes a call that already happened.
    """
    monkeypatch.setattr(
        hooks, "load_hooks", lambda: [{"event": "PostToolUse", "command": "whatever"}]
    )
    monkeypatch.setattr(
        hooks, "run_hook", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert hooks.fire_event("PostToolUse", {"session": "s"}) == []


def test_pre_tool_use_does_not_discard_a_deny_when_a_later_hook_raises(monkeypatch):
    """The specific bug: an exception mid-iteration threw away the deny.

    `pre_tool_use` used to return allow on exception, so if `fire_event` failed
    after a deny was already recorded, that deny was dropped and the tool ran.
    """
    calls = {"n": 0}

    def _explode(event, payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("event bus failed after recording a deny")
        return []

    monkeypatch.setattr(hooks, "fire_event", _explode)
    ok, reason = hooks.pre_tool_use("s", "write_file", {"path": "/etc/passwd"})
    assert ok is False
    assert "PreToolUse hooks could not be evaluated" in reason


def test_pre_tool_use_allows_when_every_handler_allows(monkeypatch):
    monkeypatch.setattr(
        hooks,
        "fire_event",
        lambda event, payload: [{"ok": True, "decision": "allow", "reason": "", "command": "c"}],
    )
    assert hooks.pre_tool_use("s", "read_file", {}) == (True, "")


def test_pre_tool_use_denies_on_the_first_deny(monkeypatch):
    """Unchanged behaviour, asserted so the fail-closed change did not reorder it."""
    monkeypatch.setattr(
        hooks,
        "fire_event",
        lambda event, payload: [
            {"ok": True, "decision": "allow", "reason": "", "command": "a"},
            {"ok": False, "decision": "deny", "reason": "nope", "command": "b"},
            {"ok": False, "decision": "deny", "reason": "later", "command": "c"},
        ],
    )
    ok, reason = hooks.pre_tool_use("s", "write_file", {})
    assert ok is False
    assert reason == "Denied by hook `b`: nope"


# --- checkpoints: silence means /rewind lies --------------------------------


def test_checkpoint_save_failure_is_reported(tmp_path, monkeypatch, capsys):
    """A checkpoint that does not save means /rewind restores stale state."""
    monkeypatch.setattr(cp, "_dir", lambda: tmp_path)
    _boom(monkeypatch, cp)
    cp._save("s", [{"n": 1, "tool": "write_file"}])
    err = capsys.readouterr().err
    assert "could not save a checkpoint" in err
    assert "OSError" in err


def test_checkpoint_save_warns_only_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cp, "_dir", lambda: tmp_path)
    _boom(monkeypatch, cp)
    for _ in range(5):
        cp._save("s", [{"n": 1}])
    err = capsys.readouterr().err
    assert err.count("could not save a checkpoint") == 1, err


def test_redo_stack_save_failure_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cp, "_dir", lambda: tmp_path)
    _boom(monkeypatch, cp)
    cp._rsave("s", [{"target": "x", "bytes_b64": ""}])
    assert "redo stack" in capsys.readouterr().err


def test_redo_clear_failure_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cp, "_dir", lambda: tmp_path)
    monkeypatch.setattr(cp.Path, "unlink", lambda *a, **kw: (_ for _ in ()).throw(OSError("busy")))
    cp._clear_redo("s")
    assert "clear the rewind redo stack" in capsys.readouterr().err


def test_snapshot_before_still_never_raises(tmp_path, monkeypatch, capsys):
    """The warning must not turn a best-effort feature into a crash.

    It still returns the would-be checkpoint number rather than failing the edit:
    the caller ignores it (agent.py), and refusing the write because bookkeeping
    failed would lose the user's work over a full disk.
    """
    monkeypatch.setattr(cp, "_dir", lambda: tmp_path)
    _boom(monkeypatch, cp)
    target = tmp_path / "f.txt"
    target.write_text("hi")
    assert cp.snapshot_before("s", "write_file", {"path": str(target)}) == 1
    assert "could not save a checkpoint" in capsys.readouterr().err


# --- egress: the audit row is the evidence ---------------------------------


def test_failed_egress_ledger_write_is_reported(monkeypatch, capsys):
    """A denied fetch whose row cannot be written leaves no evidence at all.

    `sk audit --prove` rests on the existence of an ok=0 row, so this is the one
    place where losing a row changes what the tool can claim.
    """
    import sk.store as store_mod

    def _raise(*a, **kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store_mod, "log_egress", _raise)
    eg.record("read_url", "https://evil.test/x", eg.REASON_NOT_ALLOWED)
    err = capsys.readouterr().err
    assert "could not write the egress audit row" in err
    assert "evil.test" in err


def test_egress_ledger_warning_is_not_repeated_per_fetch(monkeypatch, capsys):
    import sk.store as store_mod

    monkeypatch.setattr(store_mod, "log_egress", lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    for host in ("a.test", "b.test", "c.test"):
        eg.record("read_url", f"https://{host}/x", eg.REASON_NOT_ALLOWED)
    assert capsys.readouterr().err.count("could not write the egress audit row") == 3


def test_egress_record_still_never_raises(monkeypatch):
    import sk.store as store_mod

    monkeypatch.setattr(store_mod, "log_egress", lambda *a, **kw: 1 / 0)
    eg.record("read_url", "https://ok.test/", None)  # must not propagate


# --- store: a row that is stored but not indexed ---------------------------


class _FakeConn:
    """Minimal sqlite3.Connection stand-in that fails only on the FTS insert."""

    def __init__(self, fail_sql_pred):
        self._fail = fail_sql_pred

    def execute(self, sql, *a, **kw):
        if self._fail(sql):
            raise RuntimeError("no such table: messages_fts")
        return _FakeCur()

    def commit(self):
        pass

    def close(self):
        pass


class _FakeCur:
    rowcount = 1

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return []


def test_store_warning_is_emitted_once_per_index(monkeypatch, capsys):
    monkeypatch.setattr(store, "_connect", lambda: _FakeConn(lambda s: "messages_fts" in str(s)))
    for _ in range(4):
        store.save_message("s", "user", "hi")
    assert capsys.readouterr().err.count("not indexed for search") == 1
