"""Daemon tests: pure check logic with isolation."""

import sk.daemon as daemon
import sk.store as store


def _iso_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def test_new_failures_only_new(monkeypatch, tmp_path):
    _iso_store(tmp_path, monkeypatch)
    store.log_shell("ok one", "/tmp", 0)
    store.log_shell("bad one", "/tmp", 3)
    rows, max_id = daemon.new_failures(0)
    assert any(r[1] == "bad one" for r in rows) and all(r[1] != "ok one" for r in rows)
    rows2, _ = daemon.new_failures(max_id)
    assert rows2 == []


def test_check_once_state_advances(monkeypatch, tmp_path):
    _iso_store(tmp_path, monkeypatch)
    store.log_shell("boom cmd", "/tmp", 1)
    nudges, state = daemon.check_once({"last_shell_id": 0}, projects=[], disk_warn=99)
    assert any("boom cmd" in n for n in nudges)
    assert state["last_shell_id"] > 0
    nudges2, _ = daemon.check_once(state, projects=[], disk_warn=99)
    assert not any("boom cmd" in n for n in nudges2)


def test_disk_parse(monkeypatch):
    assert daemon.disk_use_pct() is None or 0 <= daemon.disk_use_pct() <= 100
