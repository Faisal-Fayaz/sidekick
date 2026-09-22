"""Tools submodule: approval-gated writes: make_dir, write_file, edit_file, delete_file + blocklist. (split from sk/tools.py, pure move)."""

from __future__ import annotations

from pathlib import Path


def tool_delete_file(path: str) -> str:
    """Delete a file or empty dir under HOME/tmp. Approval-gated, blocklist enforced."""

    checked = _check_write_path(path)
    if isinstance(checked, str):
        return checked
    p: Path = checked
    try:
        if not p.exists() and not p.is_symlink():
            return f"Error: {p} does not exist."
        if p.is_symlink() or p.is_file():
            p.unlink()
            return f"Deleted file {p}"
        # dir: only empty dirs, no recursion (use shell rmdir/rm with approval for more)
        try:
            p.rmdir()
            return f"Deleted empty dir {p}"
        except OSError:
            return f"Error: {p} is a non-empty directory — refusing (remove contents first)."
    except Exception as e:
        return f"Error: {e}"


WRITE_TOOLS = {"write_file", "edit_file", "make_dir"}

WRITE_BLOCKLIST = (
    Path.home() / ".ssh",
    Path.home() / ".gnupg",
    Path.home() / ".sidekick" / "history.db",
    Path("/etc"),
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
    Path("/boot"),
    Path("/proc"),
    Path("/sys"),
    Path("/dev"),
)


def _check_write_path(path: str) -> Path | str:
    import tempfile

    try:
        p = Path(path).expanduser().resolve()
    except Exception as e:
        return f"Error: bad path: {e}"
    # block both the literal entries and their resolved symlink targets
    # (macOS resolves /etc -> /private/etc; tmp dirs live under /private too)
    block_targets: list[Path] = []
    for blocked in WRITE_BLOCKLIST:
        block_targets.append(blocked)
        try:
            block_targets.append(blocked.resolve())
        except Exception:
            pass
    for blocked in block_targets:
        try:
            if p == blocked or blocked in p.parents:
                return f"Error: writes to {p} are blocked (sensitive path {blocked})."
        except Exception:
            pass
    # only allow writes under HOME or the system temp dir (incl. legacy /tmp)
    home = Path.home().resolve()
    try:
        is_home = p == home or home in p.parents
    except Exception:
        is_home = False
    tmp_root = Path(tempfile.gettempdir()).resolve()
    is_tmp = p == tmp_root or tmp_root in p.parents
    # /tmp is a symlink to /private/tmp on macOS and may differ from gettempdir()
    if not is_tmp:
        raw_tmp = str(p)
        is_tmp = raw_tmp.startswith("/tmp/") or raw_tmp.startswith("/private/tmp/")
    if not (is_home or is_tmp):
        return f"Error: writes are only allowed under {home} or {tmp_root} (got {p})."
    return p


def tool_make_dir(path: str) -> str:
    """mkdir -p under HOME or /tmp. Approval-gated like other writes."""
    checked = _check_write_path(path)
    if isinstance(checked, str):
        return checked
    p: Path = checked
    try:
        if p.exists() and not p.is_dir():
            return f"Error: {p} exists and is not a directory."
        p.mkdir(parents=True, exist_ok=True)
        return f"Created {p}"
    except Exception as e:
        return f"Error: {e}"


def tool_write_file(path: str, content: str) -> str:
    checked = _check_write_path(path)
    if isinstance(checked, str):
        return checked
    p: Path = checked
    if len(content) > 100_000:
        return "Error: content too large (>100KB), refusing."
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Wrote {len(content)} chars to {p}"
    except Exception as e:
        return f"Error: {e}"


def tool_edit_file(path: str, old_string: str, new_string: str) -> str:
    if not old_string:
        return "Error: old_string empty."
    if old_string == new_string:
        return "Error: old_string == new_string, nothing to do."
    checked = _check_write_path(path)
    if isinstance(checked, str):
        return checked
    p: Path = checked
    try:
        if not p.exists():
            return f"Error: {p} does not exist."
        if p.stat().st_size > 500_000:
            return "Error: file too large to edit."
        text = p.read_text(errors="replace")
        count = text.count(old_string)
        if count == 0:
            return "Error: old_string not found in file."
        if count > 1:
            return f"Error: old_string found {count}x, must be unique. Provide more context."
        text = text.replace(old_string, new_string, 1)
        if len(text) > 500_000:
            return "Error: result too large, refusing."
        p.write_text(text)
        return f"Edited {p} (1 replacement, {len(new_string)} chars in)"
    except Exception as e:
        return f"Error: {e}"
