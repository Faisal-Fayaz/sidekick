"""User-defined slash commands: ~/.sidekick/commands/*.md (global) plus
.sidekick/commands/*.md (project, wins on name clash).

File format: optional frontmatter (description, argument-hint), body is the
prompt template with {{args}} (or $ARGUMENTS) substitution. After args are
substituted, two expansions run (single pass, innermost-first for nesting):
- shell: !`cmd` or !{cmd} runs via sh -c (10s timeout) and inlines stdout;
  failures and timeouts inline an error note instead. Never raises.
- file: @path or @{path} (~/ and relative paths resolve against the cwd)
  inlines up to 8KB; missing files, directories, and errors inline a note.
  Bare @token always expands, so literal @-mentions (emails) become notes —
  reword them or pin the path with @{...}.
Templates are local user files: expansions run with your own shell trust
(the agent cannot invoke slash commands). Builtin slash commands always
win; colliding files are skipped (reported by custom_warnings, shown in /help).
Never raises.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9_-]+$")
MAX_BODY = 8000
SHELL_TIMEOUT = 10.0
MAX_EXPANSION_BYTES = 8192

_SHELL_RE = re.compile(r"!`([^`\n]+)`|!{([^{}]+)}")
_FILE_RE = re.compile(r"@\{([^{}]+)\}|@([A-Za-z0-9_./~+-]+)")


def _dirs() -> list[Path]:
    from .config import CONFIG_DIR

    dirs = [CONFIG_DIR / "commands"]
    try:
        proj = Path(os.getcwd()) / ".sidekick" / "commands"
        if proj.is_dir():
            dirs.append(proj)
    except Exception:
        pass
    return dirs


def list_custom_commands() -> list[tuple[str, str, Path]]:
    """(name, description, file): project entries override global ones."""
    found: dict[str, tuple[str, Path]] = {}
    for d in _dirs():
        try:
            files = sorted(d.glob("*.md"))
        except Exception:
            continue
        for f in files:
            try:
                name = f.stem.strip().lower()
                if not NAME_RE.fullmatch(name) or f.stat().st_size > MAX_BODY:
                    continue
                text = f.read_text(errors="replace")
            except Exception:
                continue
            try:
                from .skills import parse_frontmatter

                meta, body = parse_frontmatter(text)
            except Exception:
                meta, body = {}, text
            desc = str(meta.get("description", "") or "").strip()[:200]
            if not desc:
                first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
                desc = first[:120] or "custom command"
            found[name] = (desc, f)
    return [(n, d, p) for n, (d, p) in sorted(found.items())]


def custom_warnings() -> list[str]:
    """Skipped files worth telling the user about (builtin collisions)."""
    try:
        from . import slash as _slash

        builtins = {n.split()[0].lower() for n, _ in _slash.COMMANDS}
    except Exception:
        return []
    out = []
    for d in _dirs():
        try:
            files = sorted(d.glob("*.md"))
        except Exception:
            continue
        for f in files:
            name = f.stem.strip().lower()
            if NAME_RE.fullmatch(name) and name in builtins:
                out.append(f"{f}: shadows builtin /{name}, ignored")
    return out


def _run_shell(cmd: str) -> str:
    """Run cmd via sh -c, inline stdout. Errors/timeouts become notes. Never raises."""
    cmd = (cmd or "").strip()
    if not cmd:
        return ""
    try:
        r = subprocess.run(
            ["sh", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"(shell expansion timed out after {SHELL_TIMEOUT:g}s: {cmd})"
    except Exception as e:
        return f"(shell expansion failed: {cmd}: {e})"
    out = r.stdout or ""
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        detail = f"\n{err}" if err else ""
        return f"(shell expansion failed, exit {r.returncode}: {cmd}{detail})"
    if len(out) > MAX_EXPANSION_BYTES:
        out = out[:MAX_EXPANSION_BYTES] + f"\n... [truncated at {MAX_EXPANSION_BYTES} chars]"
    return out


def _read_ref(raw: str) -> str:
    """Inline file contents for @path. Missing/unreadable becomes a note. Never raises."""
    raw = (raw or "").strip()
    if not raw:
        return "@"
    try:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = Path(os.getcwd()) / p
        if not p.exists():
            return f"(file not found: {raw})"
        if not p.is_file():
            return f"(not a file: {raw})"
        text = p.read_text(errors="replace")
    except Exception as e:
        return f"(could not read {raw}: {e})"
    if len(text) > MAX_EXPANSION_BYTES:
        text = text[:MAX_EXPANSION_BYTES] + f"\n... [truncated at {MAX_EXPANSION_BYTES} chars]"
    return text


def _expand(text: str) -> str:
    """Single-pass shell + file expansion. Never raises."""
    try:
        text = _SHELL_RE.sub(
            lambda m: _run_shell(m.group(1) if m.group(1) is not None else m.group(2)), text
        )
        text = _FILE_RE.sub(
            lambda m: _read_ref(m.group(1) if m.group(1) is not None else m.group(2)), text
        )
    except Exception:
        pass
    return text


def render_custom_command(name: str, args: str) -> str | None:
    """Render the template for /name: args substituted, then !/` and @ expanded.

    None when unknown. Never raises.
    """
    key = (name or "").strip().lower()
    if not NAME_RE.fullmatch(key):
        return None
    for cand_name, _, path in list_custom_commands():
        if cand_name == key:
            try:
                from .skills import parse_frontmatter

                _, body = parse_frontmatter(path.read_text(errors="replace"))
            except Exception:
                return None
            body = body.replace("{{args}}", args or "").replace("$ARGUMENTS", args or "")
            return _expand(body)
    return None
