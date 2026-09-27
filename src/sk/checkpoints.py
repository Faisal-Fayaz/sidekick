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

CHECKPOINT_TOOLS = ("write_file", "edit_file", "delete_file")
MAX_CHECKPOINTS = 20
MAX_FILE_BYTES = 1_000_000


def _dir() -> Path:
    from .config import CONFIG_DIR

    d = CONFIG_DIR / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


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
        _path_for(session).write_text(json.dumps(recs[-MAX_CHECKPOINTS:]))
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
            return f"_no checkpoint #{n} (have #{lo}..#{hi})_"
        rec = hits[0]
    try:
        target = Path(str(rec["target"]))
        if rec.get("original_b64") is None:
            if target.is_file() or target.is_symlink():
                target.unlink()
            action = "removed created file"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(rec["original_b64"]))
            action = "restored original bytes"
        when = time.strftime("%H:%M", time.localtime(float(rec.get("ts", 0))))
        return f"_rewound #{rec['n']} ({rec['tool']} → {rec['target']}, {when}) — {action}_"
    except Exception as e:
        return f"_rewind failed ({e})_"
