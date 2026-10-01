"""Tools submodule: approval-gated writes: make_dir, write_file, edit_file, delete_file + blocklist. (split from sk/tools.py, pure move)."""

from __future__ import annotations

from pathlib import Path

from ..atomic import atomic_write_text


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


WRITE_TOOLS = {"write_file", "edit_file", "make_dir", "generate_image"}

# --- protected paths ---------------------------------------------------------
# Principle: a path the agent can write in order to GRANT ITSELF authority is
# not writable, and no allow rule lifts it. Editing any of these lets the agent
# obtain code execution that outlives the session, or redirect its own traffic,
# so they sit above the HOME/tmp allow-root rather than inside it (closes #305).
#
# Names are relative to $HOME when they do not start with "/", so the list works
# under a monkeypatched HOME (tests) as well as a real one.
_PROTECTED_HOME_NAMES = (
    # shell rc / login: instant persistence, runs on every new shell
    ".bashrc",
    ".bash_profile",
    ".bash_login",
    ".bash_aliases",
    ".bash_logout",
    ".zshrc",
    ".zprofile",
    ".zshenv",
    ".zlogin",
    ".zlogout",
    ".profile",
    ".envrc",
    # tool configuration: code execution or self-reconfiguration
    ".gitconfig",
    ".gitmodules",
    ".npmrc",
    ".yarnrc",
    ".yarnrc.yml",
    ".pnp.cjs",
    ".pnpmfile.cjs",
    "bunfig.toml",
    ".bunfig.toml",
    ".ripgreprc",
    "pyrightconfig.json",
    ".pre-commit-config.yaml",
    ".bazelrc",
    ".bazelversion",
    ".bazeliskrc",
    ".devcontainer.json",
    # credentials
    ".aws",
    ".docker",
    ".kube",
    ".netrc",
    ".gnupg",
    ".ssh",
    # the agent's own state and the CLI itself
    ".sidekick",
    ".local/bin/sk",
)
# Editor / agent / VCS metadata: hooks and task runners execute on next command.
# Matched as path COMPONENTS below the allow-root, so `<any-repo>/.git/hooks/x`
# is caught regardless of where the agent was launched from.
_PROTECTED_DIR_NAMES = frozenset(
    {".git", ".vscode", ".idea", ".husky", ".cargo", ".devcontainer", ".mcp.json", "hooks"}
)
_PROTECTED_ABS = (
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/proc",
    "/sys",
    "/dev",
)


def _protected_paths() -> list[Path]:
    """Blocked write targets, resolved. Computed per call so tests can move HOME."""
    home = Path.home()
    out: list[Path] = [Path(a) for a in _PROTECTED_ABS]
    for name in _PROTECTED_HOME_NAMES:
        out.append(home / name)
    return out


def _is_under(p: Path, root: Path) -> bool:
    try:
        return p == root or root in p.parents
    except Exception:
        return False


def _check_write_path(path: str) -> Path | str:
    import tempfile

    try:
        p = Path(path).expanduser().resolve()
    except Exception as e:
        return f"Error: bad path: {e}"
    # Block both the literal entries and their resolved symlink targets.
    # macOS resolves /etc -> /private/etc; tmp dirs live under /private too.
    # Compare PATH COMPONENTS, never string prefixes: a prefix comparison lets
    # a sibling directory sharing the prefix escape (the classic
    # `str.startswith` containment bug).
    blocked_raw = _protected_paths()
    seen: set[str] = set()
    for blocked in blocked_raw:
        for candidate in (blocked, _safe_resolve(blocked)):
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if _is_under(p, candidate):
                return f"Error: writes to {p} are blocked (protected path {candidate})."
    # only allow writes under HOME or the system temp dir (incl. legacy /tmp)
    home = Path.home().resolve()
    is_home = _is_under(p, home)
    tmp_root = Path(tempfile.gettempdir()).resolve()
    is_tmp = _is_under(p, tmp_root)
    # /tmp is a symlink to /private/tmp on macOS and may differ from gettempdir()
    if not is_tmp:
        raw_tmp = str(p)
        is_tmp = raw_tmp.startswith("/tmp/") or raw_tmp.startswith("/private/tmp/")
    if not (is_home or is_tmp):
        return f"Error: writes are only allowed under {home} or {tmp_root} (got {p})."
    # Protected dir names anywhere below the allow-root: `<repo>/.git/hooks/x`
    # executes on the next git command, and `/usr` may live under a home mount.
    root = home if is_home else tmp_root
    try:
        rel = p.relative_to(root)
    except ValueError:
        rel = p
    for part in rel.parts[:-1] or rel.parts:
        if part in _PROTECTED_DIR_NAMES:
            return f"Error: writes to {p} are blocked (protected directory {part!r})."
    return p


def _safe_resolve(p: Path) -> Path:
    try:
        return p.resolve()
    except Exception:
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


def _verify_write(p: Path, content: str) -> str | None:
    """Confirm the bytes on disk match what was requested. None when ok,
    else an error string. Silent truncation (short writes, lost tails) must
    never report success. Never raises.

    Compares content, not just size (#316). A size check cannot tell a correct
    write from a file that happens to be the right length — a stale file left by
    a failed write, or a same-length truncation — so it would pass while the
    requested content was absent. Writes are capped at 100KB, so re-reading is
    cheaper than the ambiguity it removes.
    """
    try:
        expected = content.encode("utf-8", errors="replace")
        actual = p.read_bytes()
        if actual != expected:
            return (
                f"Error: write verification failed for {p} "
                f"({len(actual)} bytes on disk, expected {len(expected)}) — retry."
            )
        return None
    except Exception as e:
        return f"Error: could not verify write to {p}: {e}"


def tool_write_file(path: str, content: str) -> str:
    checked = _check_write_path(path)
    if isinstance(checked, str):
        return checked
    p: Path = checked
    if len(content) > 100_000:
        return "Error: content too large (>100KB), refusing."
    try:
        atomic_write_text(p, content)
        problem = _verify_write(p, content)
        if problem is not None:
            return problem
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
        atomic_write_text(p, text)
        problem = _verify_write(p, text)
        if problem is not None:
            return problem
        return f"Edited {p} (1 replacement, {len(new_string)} chars in)"
    except Exception as e:
        return f"Error: {e}"


def tool_generate_image(path: str, prompt: str, size: str = "", model: str = "") -> str:
    """Generate an image via the provider images endpoint. Approval-gated."""
    from sk.config import Config
    from sk.images import generate_image

    return generate_image(Config.load(), prompt, size=size, model=model, out=path)
