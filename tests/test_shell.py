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


def test_log_shell_truncates_and_coerces(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    long_cmd, long_cwd = "x" * 2500, "/d" + "y" * 600
    assert store.log_shell(long_cmd, long_cwd, 0) is True
    rows = store.list_shell(limit=5)
    assert len(rows) == 1 and len(rows[0][1]) == 2000 and len(rows[0][2]) == 500
    assert store.log_shell("none exit", "/tmp", None) is True  # None exit → 0
    assert store.list_shell(limit=5)[0][3] == 0
    # non-consecutive dupes are fine; only exact consecutive dupes drop
    assert store.log_shell("echo again", "/tmp", 0) is True
    assert store.log_shell("something else", "/tmp", 0) is True
    assert store.log_shell("echo again", "/tmp", 0) is True


def test_list_shell_limit_clamps_and_orders(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    for i in range(5):
        assert store.log_shell(f"cmd{i}", "/tmp", 0) is True
    rows = store.list_shell(limit=3)
    assert [r[1] for r in rows] == ["cmd4", "cmd3", "cmd2"]  # newest first
    assert len(store.list_shell(limit=0)) == 5  # falsy → default
    assert len(store.list_shell(limit=-5)) == 1  # clamped to floor
    assert len(store.list_shell(limit=10**9)) == 5  # clamped to ceiling
    assert len(store.list_shell(limit="junk")) == 5  # garbage → default


def test_last_failed_most_recent(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    store.log_shell("first fail", "/a", 1)
    store.log_shell("ok", "/a", 0)
    store.log_shell("second fail", "/b", 3)
    fail = store.last_failed()
    assert fail is not None and fail[1] == "second fail" and fail[3] == 3
    assert fail[2] == "/b"


def test_skip_prefix_variants(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    assert store.log_shell("sk hook-log --cmd 'ls'", "/tmp", 0) is False
    assert store.log_shell("sk hook_log --cmd 'ls'", "/tmp", 0) is False
    assert store.log_shell("  sk hook-log --cmd 'ls'  ", "/tmp", 0) is False  # stripped first
    assert store.log_shell("run sk hook-log helper", "/tmp", 0) is False  # embedded
    assert store.log_shell("sk export s1", "/tmp", 0) is True  # other sk cmds kept
    assert store.log_shell("hook-logger --help", "/tmp", 0) is True  # prefix, not substring
