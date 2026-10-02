"""Per-session file checkpoints: snapshot agent edits, /rewind to undo.

Scope: write_file/edit_file/delete_file targets only. shell-tool mutations
are NOT snapshotted (arbitrary commands have no well-defined target) — use
git for those. Snapshots live under ~/.sidekick/checkpoints/<session>.json,
capped at MAX_CHECKPOINTS records per session. All entry points never raise.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path

from sk.atomic import atomic_write_text
from sk.fsperm import ensure_private_dir

CHECKPOINT_TOOLS = ("write_file", "edit_file", "delete_file", "generate_image")
MAX_CHECKPOINTS = 20
MAX_FILE_BYTES = 1_000_000


def _dir() -> Path:
    from .config import CONFIG_DIR

    # base64 of pre-edit file bytes: this directory and its contents must not be
    # group/other readable (#301)
    return ensure_private_dir(CONFIG_DIR / "checkpoints")


def _path_for(session: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session or "default")
    return _dir() / f"{safe}.json"


def _load(session: str) -> list[dict]:
    try:
        p = _path_for(session)
        if not p.is_file():
            return []
        recs = json.loads(p.read_text())
        return [r for r in recs if isinstance(r, dict) and isinstance(r.get("n"), int)]
    except Exception:
        return []


def _save(session: str, recs: list[dict]) -> None:
    try:
        atomic_write_text(_path_for(session), json.dumps(recs[-MAX_CHECKPOINTS:]))
    except Exception:
        pass


def _rpath_for(session: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session or "default")
    return _dir() / f"{safe}.redo.json"


def _rload(session: str) -> list[dict]:
    """Redo stack: pre-restore states pushed by rewind(), newest last."""
    try:
        p = _rpath_for(session)
        if not p.is_file():
            return []
        recs = json.loads(p.read_text())
        return [r for r in recs if isinstance(r, dict) and isinstance(r.get("target"), str)]
    except Exception:
        return []


def _rsave(session: str, recs: list[dict]) -> None:
    try:
        atomic_write_text(_rpath_for(session), json.dumps(recs[-MAX_CHECKPOINTS:]))
    except Exception:
        pass


def _push_redo(session: str, entry: dict) -> None:
    stack = _rload(session)
    stack.append(entry)
    _rsave(session, stack)


def _clear_redo(session: str) -> None:
    try:
        _rpath_for(session).unlink(missing_ok=True)
    except Exception:
        pass


def snapshot_before(session: str, tool: str, args: dict | None, cwd: str = "") -> int | None:
    """Record the pre-edit bytes of a mutating file-tool target.

    Returns the checkpoint number, or None when there is nothing to track
    (other tools, empty session/path, directories, oversize files). Never raises.
    """
    if tool not in CHECKPOINT_TOOLS or not (session or "").strip():
        return None
    raw = str(((args or {}).get("path", "")) or "").strip()
    if not raw:
        return None
    try:
        target = Path(cwd or os.getcwd(), raw).expanduser()
        if target.is_dir():
            return None
        if target.is_file():
            if target.stat().st_size > MAX_FILE_BYTES:
                return None
            original = base64.b64encode(target.read_bytes()).decode()
        else:
            original = None  # new file: rewind deletes it
        recs = _load(session)
        n = (recs[-1]["n"] + 1) if recs else 1
        recs.append(
            {
                "n": n,
                "ts": time.time(),
                "tool": tool,
                "target": str(target),
                "original_b64": original,
            }
        )
        _save(session, recs)
        _clear_redo(session)  # new write after a rewind voids the redo stack
        return n
    except Exception:
        return None


def rewind(session: str, n: int | None = None) -> str:
    """Restore checkpoint n (default: latest) into the working tree.

    Missing-original checkpoints remove the created file; otherwise the
    original bytes are written back (parents recreated). Never raises.
    """
    recs = _load(session)
    if not recs:
        return "_(no checkpoints in this session yet)_"
    if n is None:
        rec = recs[-1]
    else:
        hits = [r for r in recs if r.get("n") == n]
        if not hits:
            lo, hi = recs[0]["n"], recs[-1]["n"]
            return f"Error: no checkpoint #{n} (have #{lo}..#{hi})"
        rec = hits[0]
    try:
        target = Path(str(rec["target"]))
        try:
            cur = target.read_bytes() if (target.is_file() and not target.is_symlink()) else None
        except Exception:
            cur = None
        # Oversize pre-restore state can't be kept: skip the push rather
        # than record a lossy entry whose redo would destroy data.
        oversize = cur is not None and len(cur) > MAX_FILE_BYTES
        if rec.get("original_b64") is None:
            if target.is_file() or target.is_symlink():
                target.unlink()
            action = "removed created file"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(rec["original_b64"]))
            action = "restored original bytes"
        if not oversize:
            _push_redo(
                session,
                {
                    "target": str(target),
                    "bytes_b64": base64.b64encode(cur).decode() if cur is not None else None,
                    "tool": str(rec.get("tool", "?")),
                    "n": rec["n"],
                },
            )
        when = time.strftime("%H:%M", time.localtime(float(rec.get("ts", 0))))
        return f"_rewound #{rec['n']} ({rec['tool']} → {rec['target']}, {when}) — {action}_"
    except Exception as e:
        return f"Error: rewind failed ({e})"


def redo(session: str) -> str:
    """Re-apply the most recently rewound state (undo of /rewind).

    Pops the newest redo entry pushed by rewind() and writes its captured
    pre-restore bytes back (or removes the file when it did not exist).
    Empty stack reports cleanly. Never raises.
    """
    stack = _rload(session)
    if not stack:
        return "_(nothing to redo — rewind first)_"
    entry = stack.pop()
    _rsave(session, stack)
    try:
        target = Path(entry["target"])
        if entry.get("bytes_b64") is None:
            if target.is_file() or target.is_symlink():
                target.unlink()
            action = "removed again"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(entry["bytes_b64"]))
            action = "re-applied"
        return f"_redid ({entry.get('tool', '?')} → {entry['target']}) — {action}_"
    except Exception as e:
        _push_redo(session, entry)  # failed redo stays retryable
        return f"Error: redo failed ({e})"
