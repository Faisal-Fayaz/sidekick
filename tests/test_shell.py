"""Shell hook tests: isolated DB."""

import sk.store as store


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def test_log_and_list(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    assert store.log_shell("ls -la", "/tmp", 0) is True
    rows = store.list_shell(limit=5)
    assert len(rows) == 1 and rows[0][1] == "ls -la"


def test_skip_hook_noise_and_dupes(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    assert store.log_shell("sk hook-log --cmd x", "/tmp", 0) is False
    assert store.log_shell("  ", "/tmp", 0) is False
    assert store.log_shell("echo hi", "/tmp", 0) is True
    assert store.log_shell("echo hi", "/tmp", 0) is False  # consecutive dupe


def test_last_failed(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    assert store.last_failed() is None
    store.log_shell("good cmd", "/tmp", 0)
    store.log_shell("bad cmd", "/tmp", 2)
    fail = store.last_failed()
    assert fail is not None and fail[1] == "bad cmd" and fail[3] == 2
