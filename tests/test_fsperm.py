"""`~/.sidekick/**` must not be readable or writable by other local users (#301).

Measured before this work, with the default umask of 022:

    drwxrwxr-x  ~/.sidekick/
    -rw-r--r--  history.db        every conversation + tool result
    -rw-rw-r--  nudges.log        shell commands
    -rw-rw-r--  checkpoints/*.json base64 of pre-edit file bytes

Sidekick is local-first and stores everything it knows in `$HOME`, so on a
shared machine that is the most sensitive thing on the account. The directory
mode alone stops traversal; the file modes are defence in depth for anything we
did not create.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from sk.fsperm import (
    PRIVATE_DIR,
    PRIVATE_FILE,
    ensure_private_dir,
    private_open_append,
    repair_once,
    reset_repair_flag,
    tighten,
    tighten_home,
)


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _home(tmp_path, monkeypatch) -> Path:
    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    d = tmp_path / ".sidekick"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- the helper -------------------------------------------------------------


def test_new_dir_is_0700_not_the_umask_default(tmp_path):
    p = ensure_private_dir(tmp_path / "d")
    assert _mode(p) == PRIVATE_DIR


def test_dir_mode_is_set_explicitly_not_left_to_umask(tmp_path):
    """mkdir(mode=) is still filtered by the umask, so a restrictive umask would
    otherwise produce 0000 rather than 0700."""
    old = os.umask(0o077)
    try:
        p = ensure_private_dir(tmp_path / "d")
        assert _mode(p) == PRIVATE_DIR
    finally:
        os.umask(old)


def test_existing_wide_dir_is_tightened(tmp_path):
    p = tmp_path / "d"
    p.mkdir()
    os.chmod(p, 0o775)
    ensure_private_dir(p)
    assert _mode(p) == PRIVATE_DIR


def test_tighten_preserves_owner_bits():
    p = __import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "f"
    p.write_text("x")
    os.chmod(p, 0o640)
    tighten(p)
    assert _mode(p) == 0o600  # group/other gone, owner rwx intact


def test_tighten_force_mode():
    p = __import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "f"
    p.write_text("x")
    os.chmod(p, 0o666)
    assert tighten(p, want=PRIVATE_FILE)
    assert _mode(p) == PRIVATE_FILE


def test_tighten_skips_symlinks(tmp_path):
    """A symlink inside ~/.sidekick must not be used to chmod outside it."""
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    os.chmod(outside, 0o666)
    link = tmp_path / "link.txt"
    link.symlink_to(outside)
    assert tighten(link, want=PRIVATE_FILE) is False
    assert _mode(outside) == 0o666, "tighten followed a symlink out of the tree"


def test_tighten_never_raises_on_missing_path(tmp_path):
    assert tighten(tmp_path / "nope") is False


def test_private_open_append_creates_0600(tmp_path):
    p = tmp_path / "log"
    with private_open_append(p) as f:
        f.write("x")
    assert _mode(p) == PRIVATE_FILE


def test_tighten_home_walks_recursively(tmp_path):
    root = tmp_path / "home"
    (root / "sub" / "deeper").mkdir(parents=True)
    for p in (root, root / "sub", root / "sub" / "deeper"):
        os.chmod(p, 0o777)
    f = root / "sub" / "deeper" / "f.txt"
    f.write_text("x")
    os.chmod(f, 0o666)

    changed = tighten_home(root)

    assert len(changed) == 4
    assert _mode(root) == PRIVATE_DIR
    assert _mode(root / "sub" / "deeper") == PRIVATE_DIR
    assert _mode(f) == PRIVATE_FILE


def test_tighten_home_is_idempotent(tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    f = root / "f"
    f.write_text("x")
    assert tighten_home(root)
    assert tighten_home(root) == [], "second pass changed something"


def test_tighten_home_missing_dir_is_a_no_op(tmp_path):
    assert tighten_home(tmp_path / "nope") == []


def test_repair_once_only_sweeps_the_first_time(tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    os.chmod(root, 0o777)
    assert len(repair_once(root)) == 1
    os.chmod(root, 0o777)  # drift again
    assert repair_once(root) == [], "repaired twice in one process"


# --- every sink, measured ---------------------------------------------------


def test_config_dir_is_0700_after_ensure_created(tmp_path, monkeypatch):
    from sk.config import Config

    home = _home(tmp_path, monkeypatch)
    os.chmod(home, 0o755)
    Config.load().ensure_created()
    assert _mode(home) == PRIVATE_DIR


def test_everything_under_home_has_no_group_or_other_bits(tmp_path, monkeypatch):
    """The acceptance criterion, asserted against every sink that exists."""
    import sk.checkpoints as cp
    import sk.daemon as daemon
    import sk.jobs as jobs
    import sk.store as store
    from sk.config import Config
    from sk.tui.helpers import _save_history, log_error

    home = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(jobs, "JOBS_PATH", home / "jobs.json")
    monkeypatch.setattr(jobs, "_LAST_LOAD_ERROR", "")
    monkeypatch.setattr(daemon, "STATE_PATH", home / "daemon.json")
    monkeypatch.setattr(daemon, "_LAST_STATE_ERROR", "")
    monkeypatch.setattr(daemon, "NUDGES_LOG", home / "nudges.log")
    monkeypatch.setattr(store, "DB_PATH", home / "history.db")

    cfg = Config.load()
    cfg.api_key = "sk-secret-value"
    cfg.save()
    jobs.save_jobs({"a": 1})
    daemon.save_state({"last_shell_id": 1})
    daemon.append_log(["ran: ls -la /home"])
    _save_history(["a secret prompt"])
    log_error("boom", ValueError("x"))
    cp._save("s1", [{"tool": "write_file", "before": "c2VjcmV0"}])
    store._connect().close()
    cfg.ensure_created()

    assert _mode(home) == PRIVATE_DIR
    expected = {
        "config.toml",
        "history.db",
        "jobs.json",
        "daemon.json",
        "nudges.log",
        "input_history",
        "tui-errors.log",
    }
    for name in expected:
        p = home / name
        assert p.exists(), f"{name} was not created by the code path under test"
        assert _mode(p) == PRIVATE_FILE, f"{name} is {oct(_mode(p))}, expected 0600"

    ckpt = cp._path_for("s1")
    assert _mode(ckpt) == PRIVATE_FILE, f"checkpoint is {oct(_mode(ckpt))}"


def test_history_db_is_not_world_readable(tmp_path, monkeypatch):
    """sqlite3.connect creates at the umask default; the fix must not rely on
    that happening to be restrictive."""
    import sk.store as store

    home = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(store, "DB_PATH", home / "history.db")
    conn = store._connect()
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    assert _mode(home / "history.db") == PRIVATE_FILE


def test_api_key_absent_does_not_relax_the_mode(tmp_path, monkeypatch):
    """The old chmod ran only when an api_key was set. The guarantee is not
    conditional on holding a credential, and must not read as though it were."""
    import sk.store as store
    from sk.config import Config

    home = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(store, "DB_PATH", home / "history.db")
    cfg = Config.load()
    cfg.api_key = ""
    cfg.save()
    cfg.max_steps = 7
    cfg.save()
    assert _mode(home / "config.toml") == PRIVATE_FILE


def test_nudges_log_stays_0600_when_appended_repeatedly(tmp_path, monkeypatch):
    import sk.daemon as daemon

    home = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(daemon, "NUDGES_LOG", home / "nudges.log")
    for i in range(3):
        daemon.append_log([f"cmd {i}"])
    assert _mode(home / "nudges.log") == PRIVATE_FILE
    assert len((home / "nudges.log").read_text().strip().splitlines()) == 3


def test_existing_world_writable_log_is_repaired_on_write(tmp_path, monkeypatch):
    """An append-only file keeps its old mode when opened; it must be tightened
    explicitly or a pre-existing 0664 stays world-writable forever."""
    import sk.daemon as daemon

    home = _home(tmp_path, monkeypatch)
    log = home / "nudges.log"
    log.write_text("old\n")
    os.chmod(log, 0o666)
    monkeypatch.setattr(daemon, "NUDGES_LOG", log)
    daemon.append_log(["new"])
    assert _mode(log) == PRIVATE_FILE


def test_legacy_install_is_repaired_on_first_cli_use(tmp_path, monkeypatch):
    """Migration: files written by earlier versions have no group/other bits
    removed until something touches them. ensure_created() is on the CLI hot
    path, so it is where that has to happen."""
    from sk.config import Config

    home = _home(tmp_path, monkeypatch)
    os.chmod(home, 0o755)
    for name, mode in (("history.db", 0o644), ("nudges.log", 0o664), ("config.toml", 0o644)):
        p = home / name
        p.write_text("{}")
        os.chmod(p, mode)

    Config.load().ensure_created()

    assert _mode(home) == PRIVATE_DIR
    for name in ("history.db", "nudges.log", "config.toml"):
        assert _mode(home / name) == PRIVATE_FILE, name


def test_repair_leaves_unrelated_modes_alone_outside_home(tmp_path, monkeypatch):
    """tighten_home is scoped. The repo writes user files that must stay
    world-readable — SIDEKICK.md in someone's project, for instance."""
    _home(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    doc = repo / "SIDEKICK.md"
    doc.write_text("# notes")
    os.chmod(doc, 0o644)
    tighten_home(tmp_path / ".sidekick")
    assert _mode(doc) == 0o644


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_mode_assertions_are_meaningful(tmp_path):
    """If chmod is a no-op here, every other assertion in this file is vacuous."""
    p = tmp_path / "f"
    p.write_text("x")
    os.chmod(p, 0o666)
    assert _mode(p) == 0o666
    assert _mode(p) != PRIVATE_FILE


def test_config_load_repairs_home_so_the_tui_is_covered(tmp_path, monkeypatch):
    """Regression: the migration only ran from ensure_created(), which the TUI
    never calls — it calls Config.load() directly. So it reached `sk <command>`
    users and nobody else, and the primary interface kept its world-readable
    transcript (#301 follow-up). Proven against the real usage path.
    """
    import sk.config as config_mod

    home = tmp_path / ".sidekick"
    home.mkdir()
    for name, mode in (("history.db", 0o644), ("nudges.log", 0o664)):
        p = home / name
        p.write_text("{}")
        os.chmod(p, mode)
    os.chmod(home, 0o755)

    monkeypatch.setattr(config_mod, "CONFIG_DIR", home)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", home / "config.toml")
    reset_repair_flag()

    config_mod.Config.load()  # what the TUI does; ensure_created is never reached

    assert _mode(home) == PRIVATE_DIR
    for name in ("history.db", "nudges.log"):
        assert _mode(home / name) == PRIVATE_FILE, name


def test_load_is_cheap_in_steady_state(tmp_path, monkeypatch):
    """Repair runs once per process, so repeat loads must not re-walk the tree."""
    import sk.config as config_mod

    home = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", home)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", home / "config.toml")
    reset_repair_flag()

    config_mod.Config.load()
    os.chmod(home, 0o777)
    config_mod.Config.load()

    assert _mode(home) == PRIVATE_DIR  # repaired before the second load, not after
