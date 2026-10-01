"""Atomic file writes (#316).

Every config, job record, daemon state file and agent-authored file was written
with `Path.write_text`, which is truncate-then-write: it opens the destination
for writing — destroying the old contents — and only then starts writing bytes.
A crash, a full disk, or a Ctrl-C in that window leaves a truncated or empty
file where something real used to be. For `jobs.json` and the daemon state that
is worse than a failed write, because the readers below treated an unparseable
file as "no data", so the next save overwrote the whole registry.

The fix is the standard POSIX dance, and it needs no dependency:

1. create a temp file **in the same directory** (same filesystem, so `replace`
   is atomic — a temp file in /tmp would not be, and would silently degrade to
   a copy),
2. write, flush, `fsync` so the bytes are really on the device,
3. `os.replace`, which is atomic and cannot fail halfway.

The reader either sees the complete old file or the complete new one. Never a
half-written one.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> Path:
    """Write `text` to `path` atomically. Returns `path`. Raises on failure.

    `mode` sets permissions on the new file (e.g. 0o600 for a secret). It is
    applied to the temp file *before* the replace, so the destination is never
    briefly world-readable with the new contents in place (#301).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        # KeyboardInterrupt lands here too. Leaving the temp file behind would
        # litter the user's directory with partial writes.
        try:
            tmp.unlink()
        except Exception:
            pass
        raise
    return target


def quarantine(path: str | Path, label: str = "") -> Path | None:
    """Move a corrupt file aside instead of discarding it. Returns its new path.

    Used when a state file will not parse. Silently returning `{}` would let the
    next save overwrite the registry with an empty one, destroying whatever the
    corrupt file still held — a truncated file is usually *mostly* intact, so it
    is worth keeping. Returns None if the move fails; the caller must not depend
    on it having worked.
    """
    src = Path(path)
    try:
        if not src.exists():
            return None
        # `jobs.json` + label "jobs" would otherwise give `jobs.json.jobs.corrupt`
        tag = label if label and label != src.stem else ""
        stem = f"{src.name}.{tag}.corrupt" if tag else f"{src.name}.corrupt"
        dest = src.with_name(stem)
        n = 0
        while dest.exists():
            n += 1
            dest = src.with_name(f"{stem}.{n}")
        src.replace(dest)
        return dest
    except Exception:
        return None
