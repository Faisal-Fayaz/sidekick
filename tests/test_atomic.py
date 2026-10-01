"""Atomic writes and corrupt-state handling (#316).

The bug: `Path.write_text` is truncate-then-write. Between opening the
destination and finishing the bytes, the old contents are already gone, so a
crash, a full disk or a Ctrl-C left an empty or half file where real data used
to be — and two readers treated "unparseable" as "no data", so the next save
overwrote the whole registry.

These tests simulate the interruption directly rather than trusting that a
working `write_text` happens to survive.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sk.atomic import atomic_write_text, quarantine


# --- the helper -------------------------------------------------------------


def test_write_then_read(tmp_path):
    p = tmp_path / "a.txt"
    atomic_write_text(p, "hello")
    assert p.read_text() == "hello"


def test_overwrite_leaves_no_temp_files(tmp_path):
    p = tmp_path / "a.txt"
    atomic_write_text(p, "one")
    atomic_write_text(p, "two")
    assert p.read_text() == "two"
    assert [x.name for x in tmp_path.iterdir()] == ["a.txt"]


def test_mode_applied_before_content_is_visible(tmp_path):
    """The destination must never be briefly world-readable with new contents."""
    p = tmp_path / "secret.toml"
    atomic_write_text(p, "api_key = 'x'", mode=0o600)
    assert p.stat().st_mode & 0o777 == 0o600


def test_temp_file_is_removed_when_replace_fails(tmp_path, monkeypatch):
    """A full disk must not leave partial files scattered in the user's dir."""
    p = tmp_path / "a.txt"
    atomic_write_text(p, "before")

    def _boom(*a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        atomic_write_text(p, "after")
    assert p.read_text() == "before"  # old content intact
    assert [x.name for x in tmp_path.iterdir()] == ["a.txt"]


def test_temp_file_removed_on_keyboard_interrupt(tmp_path, monkeypatch):
    """Ctrl-C mid-write is the common case, and it must clean up too."""

    def _boom(*a, **kw):
        raise KeyboardInterrupt

    p = tmp_path / "a.txt"
    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(KeyboardInterrupt):
        atomic_write_text(p, "x")
    assert list(tmp_path.iterdir()) == []


def test_creates_parent_directories(tmp_path):
    p = tmp_path / "deep" / "nested" / "a.txt"
    atomic_write_text(p, "x")
    assert p.read_text() == "x"


# --- quarantine -------------------------------------------------------------


def test_quarantine_preserves_the_corrupt_file(tmp_path):
    """A truncated file is usually mostly intact, so it is kept, not dropped."""
    p = tmp_path / "jobs.json"
    p.write_text('{"a": 1, "b": tr')
    moved = quarantine(p, "jobs")
    assert moved is not None and moved.exists()
    assert moved.read_text() == '{"a": 1, "b": tr'
    assert not p.exists()


def test_quarantine_does_not_clobber_an_existing_backup(tmp_path):
    p = tmp_path / "jobs.json"
    p.write_text("new")
    (tmp_path / "jobs.json.corrupt").write_text("older")
    moved = quarantine(p, "jobs")
    assert (tmp_path / "jobs.json.corrupt").read_text() == "older"
    assert moved.name == "jobs.json.corrupt.1"
    assert moved.read_text() == "new"


def test_quarantine_missing_file_returns_none(tmp_path):
    assert quarantine(tmp_path / "nope.json", "jobs") is None


def test_quarantine_failure_returns_none(tmp_path, monkeypatch):
    p = tmp_path / "jobs.json"
    p.write_text("x")
    monkeypatch.setattr(Path, "replace", lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    assert quarantine(p, "jobs") is None


# --- write_file / edit_file -------------------------------------------------


def test_write_file_is_atomic(monkeypatch, tmp_path):
    from sk.tools.write import tool_write_file

    target = tmp_path / "f.txt"
    target.write_text("OLD CONTENT THAT MATTERS")
    monkeypatch.setattr(os, "replace", lambda *a, **kw: (_ for _ in ()).throw(OSError("full")))
    out = tool_write_file(str(target), "new")
    assert out.startswith("Error:")
    assert target.read_text() == "OLD CONTENT THAT MATTERS"


def test_write_file_verification_detects_wrong_length_content(monkeypatch, tmp_path):
    """The old size check could not tell a correct write from a stale file."""
    from sk.tools.write import tool_write_file

    target = tmp_path / "f.txt"
    target.write_text("XXXXX")  # same length as what we will request
    out = tool_write_file(str(target), "abcde")
    assert "verification failed" in out or out.startswith("Wrote")


def test_verify_write_catches_same_size_wrong_content(tmp_path):
    """Directly: a size check passes this, a content check must not."""
    from sk.tools.write import _verify_write

    p = tmp_path / "f.txt"
    p.write_text("XXXXX")
    assert p.stat().st_size == len("abcde")
    assert _verify_write(p, "abcde") is not None
    assert _verify_write(p, "XXXXX") is None


def test_edit_file_verifies_and_is_atomic(monkeypatch, tmp_path):
    from sk.tools.write import tool_edit_file

    target = tmp_path / "f.txt"
    target.write_text("hello world")
    monkeypatch.setattr(os, "replace", lambda *a, **kw: (_ for _ in ()).throw(OSError("full")))
    out = tool_edit_file(str(target), "world", "there")
    assert out.startswith("Error:")
    assert target.read_text() == "hello world"


def test_edit_file_succeeds_normally(tmp_path):
    from sk.tools.write import tool_edit_file

    target = tmp_path / "f.txt"
    target.write_text("hello world")
    assert tool_edit_file(str(target), "world", "there").startswith("Edited")
    assert target.read_text() == "hello there"


# --- jobs.json --------------------------------------------------------------


def _iso_jobs(tmp_path, monkeypatch):
    import sk.jobs as jobs

    monkeypatch.setattr(jobs, "JOBS_PATH", tmp_path / "jobs.json")
    monkeypatch.setattr(jobs, "_LAST_LOAD_ERROR", "")
    return jobs


def test_jobs_missing_file_is_simply_empty(tmp_path, monkeypatch):
    jobs = _iso_jobs(tmp_path, monkeypatch)
    assert jobs.load_jobs() == {}
    assert jobs.last_load_error() == ""


def test_jobs_corrupt_file_is_never_silently_empty(tmp_path, monkeypatch):
    jobs = _iso_jobs(tmp_path, monkeypatch)
    jobs.JOBS_PATH.write_text('{"id1": {"status": "done"}')  # truncated
    assert jobs.load_jobs() == {}
    err = jobs.last_load_error()
    assert "unreadable" in err
    assert "moved aside" in err


def test_jobs_corrupt_file_is_preserved_not_overwritten(tmp_path, monkeypatch):
    """The real damage: load returned {}, then save wiped the registry."""
    jobs = _iso_jobs(tmp_path, monkeypatch)
    jobs.JOBS_PATH.write_text('{"id1": {"status": "don')
    assert jobs.load_jobs() == {}
    jobs.save_jobs({})
    # the old bytes must survive somewhere
    salvaged = list(tmp_path.glob("jobs.json.corrupt*"))
    assert len(salvaged) == 1
    assert "id1" in salvaged[0].read_text()


def test_jobs_wrong_shape_is_reported(tmp_path, monkeypatch):
    jobs = _iso_jobs(tmp_path, monkeypatch)
    jobs.JOBS_PATH.write_text("[1, 2, 3]")
    assert jobs.load_jobs() == {}
    assert "did not contain an object" in jobs.last_load_error()


def test_jobs_round_trip(tmp_path, monkeypatch):
    jobs = _iso_jobs(tmp_path, monkeypatch)
    jobs.save_jobs({"a": {"status": "done", "created": 1}})
    assert jobs.load_jobs() == {"a": {"status": "done", "created": 1}}
    assert jobs.last_load_error() == ""


def test_jobs_save_is_atomic(tmp_path, monkeypatch):
    jobs = _iso_jobs(tmp_path, monkeypatch)
    jobs.save_jobs({"a": {"status": "done"}})
    before = jobs.JOBS_PATH.read_text()
    monkeypatch.setattr(os, "replace", lambda *a, **kw: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError):
        jobs.save_jobs({"a": {"status": "error"}})
    assert jobs.JOBS_PATH.read_text() == before


# ---- daemon state ----------------------------------------------------------


def _iso_state(tmp_path, monkeypatch):
    import sk.daemon as daemon

    monkeypatch.setattr(daemon, "STATE_PATH", tmp_path / "daemon.json")
    monkeypatch.setattr(daemon, "_LAST_STATE_ERROR", "")
    return daemon


def test_daemon_missing_state_is_defaults(tmp_path, monkeypatch):
    d = _iso_state(tmp_path, monkeypatch)
    assert d.load_state() == {"last_shell_id": 0, "last_run": 0}
    assert d.last_state_error() == ""


def test_daemon_corrupt_state_is_reported_and_kept(tmp_path, monkeypatch):
    """Resetting last_shell_id to 0 silently re-fires every nudge (#316)."""
    d = _iso_state(tmp_path, monkeypatch)
    d.STATE_PATH.write_text('{"last_shell_id": 42, "last_')
    assert d.load_state() == {"last_shell_id": 0, "last_run": 0}
    assert "unreadable" in d.last_state_error()
    salvaged = list(tmp_path.glob("daemon.json.daemon-state.corrupt*"))
    assert salvaged and "42" in salvaged[0].read_text()


def test_daemon_state_round_trip(tmp_path, monkeypatch):
    d = _iso_state(tmp_path, monkeypatch)
    d.save_state({"last_shell_id": 9, "last_run": 1})
    assert d.load_state() == {"last_shell_id": 9, "last_run": 1}
    assert d.last_state_error() == ""


# ---- other sinks -----------------------------------------------------------


def test_config_save_is_atomic(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    cfg = config_mod.Config.load()
    cfg.max_steps = 5
    cfg.save()
    before = config_mod.CONFIG_PATH.read_text()
    monkeypatch.setattr(os, "replace", lambda *a, **kw: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError):
        cfg.max_steps = 6
        cfg.save()
    assert config_mod.CONFIG_PATH.read_text() == before


def test_input_history_is_atomic(tmp_path, monkeypatch):
    import sk.config as config_mod
    from sk.tui.helpers import _save_history

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    _save_history(["a", "b"])
    hist = config_mod.CONFIG_DIR / "input_history"
    assert json.loads(hist.read_text()) == ["a", "b"]
    monkeypatch.setattr(os, "replace", lambda *a, **kw: (_ for _ in ()).throw(OSError("full")))
    _save_history(["c"])  # must not raise, and must not damage the file
    assert json.loads(hist.read_text()) == ["a", "b"]


def test_cli_jobs_surfaces_corruption(tmp_path, monkeypatch):
    import sk.jobs as jobs
    from typer.testing import CliRunner

    from sk.cli import app

    monkeypatch.setattr(jobs, "JOBS_PATH", tmp_path / "jobs.json")
    monkeypatch.setattr(jobs, "_LAST_LOAD_ERROR", "")
    jobs.JOBS_PATH.write_text("{oops")
    out = CliRunner().invoke(app, ["jobs"]).output
    assert "unreadable" in out
    assert "no background jobs yet" in out  # still explains the empty list
