"""Permissions for `~/.sidekick/**` (#301).

Sidekick is local-first and stores everything it knows under `~/.sidekick`: the
full chat transcript and tool results, shell commands the agent ran, the last
100 prompts, and base64 of file bytes it was about to edit. On a shared machine
that is the most sensitive thing on the account.

It was all created with the process umask, which is `022` by default. Measured on
this repo before this module existed:

    drwxrwxr-x  ~/.sidekick/
    -rw-r--r--  history.db         every conversation + tool result
    -rw-rw-r--  nudges.log         shell commands
    -rw-rw-r--  checkpoints/*.json base64 of pre-edit file bytes

So any local user could read every conversation, and two files could be written
by anyone.

## Two rules, and why they differ

**Directories are 0700.** That alone stops traversal, which protects every file
beneath it regardless of its own mode — defence in depth for anything we did not
create or do not know about yet.

**Files are 0600**, and group/other bits are cleared on anything already on disk,
so an existing install is repaired rather than left exposed until its next write.
Owner bits are always preserved: this tightens access, it never changes what the
user can do with their own files.

Symlinks are skipped rather than followed, so a link inside `~/.sidekick` cannot
be used to chmod something outside it.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600

# Bits that must never be set on anything we own.
_GROUP_OTHER = 0o077


def tighten(path: str | Path, *, want: int | None = None) -> bool:
    """Clear group/other access on `path`. True if anything changed.

    With `want`, the owner bits are set to those of `want` as well (used to force
    0600 on files that ended up group-writable through an odd path). Never
    raises. Skips symlinks rather than following them.
    """
    try:
        p = Path(path)
        if p.is_symlink():
            return False
        st = p.lstat()
        mode = stat.S_IMODE(st.st_mode)
        new = mode & ~_GROUP_OTHER
        if want is not None:
            new = want
        if new == mode:
            return False
        os.chmod(p, new)
        return True
    except Exception:
        return False


def ensure_private_dir(path: str | Path, *, want: int = PRIVATE_DIR) -> Path:
    """Create `path` as 0700, or tighten it if it already exists. Never raises.

    `mkdir(mode=...)` is still subject to the umask, so the mode is set
    explicitly afterwards rather than trusted.
    """
    p = Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        return p
    tighten(p, want=want)
    return p


def private_open_append(path: str | Path):
    """Open for append, creating at 0600 — for files that grow (`nudges.log`).

    `open(path, "a")` would create at 0644 and there is no way to pass a mode, so
    the file descriptor is made directly. The mode applies only when the file is
    created, so an existing world-writable file still needs `tighten`.
    """
    fd = os.open(os.fspath(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE_FILE)
    return os.fdopen(fd, "a", encoding="utf-8")


def tighten_home(root: str | Path, *, want_files: int = PRIVATE_FILE) -> list[str]:
    """Tighten `root` and everything beneath it. Returns what changed. Never raises.

    A no-op when the directory does not exist, so it is safe to call from any
    startup path. Owner bits are preserved; only group/other access is removed.
    """
    changed: list[str] = []
    base = Path(root)
    try:
        if not base.is_dir() or base.is_symlink():
            return changed
    except Exception:
        return changed

    if tighten(base, want=PRIVATE_DIR):
        changed.append(str(base))
    try:
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            for name in dirnames:
                p = Path(dirpath) / name
                if p.is_symlink():
                    continue
                if tighten(p, want=PRIVATE_DIR):
                    changed.append(str(p))
            for name in filenames:
                p = Path(dirpath) / name
                if p.is_symlink():
                    continue
                if tighten(p, want=want_files):
                    changed.append(str(p))
    except Exception:
        pass
    return changed


# One repair per process, not per call. tighten_home walks the tree, and
# _connect() — the store's entry point — is called on effectively every message,
# so an unguarded sweep there would cost a full walk per operation. Every sink
# also tightens its own file on write, so the sweep only has to happen once to
# catch files that predate this module.
_repaired = False


def repair_once(root: str | Path, *, want_files: int = PRIVATE_FILE) -> list[str]:
    """tighten_home, at most once per process. Returns what it changed."""
    global _repaired
    if _repaired:
        return []
    _repaired = True
    return tighten_home(root, want_files=want_files)


def reset_repair_flag() -> None:
    """Test seam: forget that a repair has run."""
    global _repaired
    _repaired = False
