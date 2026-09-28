"""User-defined slash commands: ~/.sidekick/commands/*.md (global) plus
.sidekick/commands/*.md (project, wins on name clash).

File format: optional frontmatter (description, argument-hint), body is the
prompt template with {{args}} (or $ARGUMENTS) substitution. Invoking /name
sends the rendered template as an agent prompt. Builtin slash commands always
win; colliding files are skipped (reported by custom_warnings, shown in /help).
Never raises.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9_-]+$")
MAX_BODY = 8000


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


def render_custom_command(name: str, args: str) -> str | None:
    """Render the template for /name with args substituted. None when unknown."""
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
            return body.replace("{{args}}", args or "").replace("$ARGUMENTS", args or "")
    return None
