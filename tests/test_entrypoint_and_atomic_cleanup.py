"""`python -m sk` and the atomic-write cleanup path (#370).

Both were coverage holes with no behavioural test:

- `src/sk/__main__.py` at 0% -- the entrypoint packaging relies on
  (`python -m sk`) was never executed once, so a broken import or a missing
  `app` attribute would only surface for users who installed from a wheel.
- `atomic.py`'s `except BaseException` cleanup, the branch that stops a
  `KeyboardInterrupt` mid-write from leaving a `.tmp` file in the user's
  directory. It was introduced by the atomic-writes change and never tested,
  precisely because triggering it needs a real interrupt.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

import sk.atomic as atomic


# --- the atomic write's cleanup on interrupt --------------------------------


def _temps_in(d):
    return [p.name for p in d.iterdir() if p.name.endswith(".tmp")]


def test_interrupt_during_write_leaves_no_temp_file(tmp_path, monkeypatch):
    """The whole point of the BaseException branch: a Ctrl-C mid-write must not
    litter the user's directory with a partial `.tmp`."""
    target = tmp_path / "config.toml"

    real_fsync = os.fsync

    def _interrupt(fd):
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "fsync", _interrupt)
    with pytest.raises(KeyboardInterrupt):
        atomic.atomic_write_text(target, "data")
    assert _temps_in(tmp_path) == []
    assert not target.exists(), "a partial write must not reach the destination"
    monkeypatch.setattr(os, "fsync", real_fsync)


def test_system_exit_during_write_also_cleans_up(tmp_path, monkeypatch):
    """BaseException, not Exception: a SystemExit or a cancel is the same risk."""
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(SystemExit(1)))
    target = tmp_path / "f.txt"
    with pytest.raises(SystemExit):
        atomic.atomic_write_text(target, "data")
    assert _temps_in(tmp_path) == []


def test_cleanup_failure_does_not_mask_the_original_error(tmp_path, monkeypatch):
    """If unlink also fails, the interrupt must still propagate -- losing Ctrl-C
    in favour of a confusing unlink error would be worse."""
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(KeyboardInterrupt))
    monkeypatch.setattr(
        atomic.Path, "unlink", lambda *a, **kw: (_ for _ in ()).throw(OSError("busy"))
    )
    with pytest.raises(KeyboardInterrupt):
        atomic.atomic_write_text(tmp_path / "f.txt", "data")


def test_successful_write_leaves_no_temp_file(tmp_path):
    target = tmp_path / "f.txt"
    atomic.atomic_write_text(target, "hello")
    assert _temps_in(tmp_path) == []
    assert target.read_text() == "hello"


# --- `python -m sk` ---------------------------------------------------------


def _run_module(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "sk", *args],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=cwd,
    )


def test_python_dash_m_reports_the_version(tmp_path):
    """`python -m sk --version` is how a source install is smoke-tested, and the
    module had never been executed by the suite at all."""
    r = _run_module(["--version"], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "sk" in r.stdout
    import sk

    assert sk.__version__ in r.stdout


def test_python_dash_m_shows_help_without_launching_the_tui(tmp_path):
    """`python -m sk --help` must not fall through to launch(): the default
    subcommand is the fullscreen TUI, which would hang an unattended run."""
    r = _run_module(["--help"], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "chat" in r.stdout
