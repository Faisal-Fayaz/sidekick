"""Skills: local packs (*.md) + SKILL.md bundles (superpowers-style subdirs).

Progressive disclosure: the prompt gets a compact INDEX (name: description)
plus full bodies of tiny local packs. Full bundle bodies load on demand via
the `skill` tool, so 15×11KB of superpowers costs ~1KB of context.
"""

from __future__ import annotations

import re
from pathlib import Path

SKILLS_DIR = Path.home() / ".sidekick" / "skills"
MAX_FILES = 10
MAX_CHARS = 1500
MAX_BODY_CHARS = 8000

# preset name -> git URL for `sk skills-install`
PRESET_REPOS: dict[str, str] = {
    "superpowers": "https://github.com/obra/superpowers",
}

BUILTIN_GIT = """# git skill
- Prefer `git status`, `git diff --stat`, `git log --oneline -5` for inspection.
- Never run `git push --force`, `git reset --hard`, `git clean -fd` without explicit user approval words.
- For "what changed": status + diff stat first, then summarize.
"""

BUILTIN_DISK = """# disk skill
- For disk questions use `df -h /` and `du -sh <dir>/*` (no pipes).
- Warn when / use% >= 90. Suggest: npm cache, Downloads dupes, old ollama models.
"""


def ensure_defaults() -> Path:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    defaults = {"git.md": BUILTIN_GIT, "disk.md": BUILTIN_DISK}
    for name, content in defaults.items():
        p = SKILLS_DIR / name
        if not p.exists():
            p.write_text(content)
    return SKILLS_DIR


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split YAML-ish frontmatter. Returns (meta, body). No yaml dep."""
    m = re.match(r"\s*---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not m:
        return ({}, text)
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return (meta, m.group(2))


def _packs() -> list[tuple[str, str, Path]]:
    """All skill packs: (name, description, file). Local .md + SKILL.md bundles."""
    ensure_defaults()
    packs: list[tuple[str, str, Path]] = []
    for f in sorted(SKILLS_DIR.glob("*.md")):
        try:
            if f.stat().st_size > 20_000:
                continue
            text = f.read_text(errors="replace")
            meta, _ = parse_frontmatter(text)
            desc = meta.get("description", "") or (text.splitlines()[0][:120] if text else "")
            packs.append((meta.get("name", f.stem), desc, f))
        except Exception:
            pass
    for f in sorted(p for p in SKILLS_DIR.glob("**/SKILL.md") if p.is_file())[:30]:
        try:
            if f.stat().st_size > 100_000:
                continue
            text = f.read_text(errors="replace")
            meta, _ = parse_frontmatter(text)
            name = meta.get("name", f.parent.name)
            desc = meta.get("description", "")[:300]
            packs.append((name, desc, f))
        except Exception:
            pass
    return packs[:30]


def load_skills(query: str = "", top: int = 8) -> str:
    try:
        from .store import _keywords
    except Exception:
        _keywords = None  # type: ignore
    try:
        packs = _packs()
        if not packs:
            return "(no skills — add ~/.sidekick/skills/*.md or `sk skills-install superpowers`)"
        # relevance rank: local packs always first, then keyword overlap
        keys = set(_keywords(query)) if callable(_keywords) and query else set()

        def score(p: tuple[str, str, Path]) -> tuple[int, str]:
            name, desc, f = p
            if f.name != "SKILL.md":
                return (2, name)  # tiny local packs always in
            if not keys:
                return (1, name)
            hay = f"{name} {desc}".lower()
            return (1 if any(k in hay for k in keys) else 0, name)

        ranked = sorted(packs, key=score, reverse=True)
        # keep all locals + top relevant bundles within budget
        chosen = [p for p in ranked if p[2].name != "SKILL.md"]
        chosen += [p for p in ranked if p[2].name == "SKILL.md"][: max(0, top - len(chosen))]
        lines = [f"- {name}: {desc}"[:160] if desc else f"- {name}" for name, desc, _ in chosen]
        index = "SKILL INDEX (call `skill` with a name to load full instructions):\n" + "\n".join(lines)
        bodies: list[str] = []
        for name, _, f in chosen:
            if f.name == "SKILL.md" or f.stat().st_size > 2000:
                continue
            try:
                _, body = parse_frontmatter(f.read_text(errors="replace"))
                bodies.append(f"[{name}]\n{body.strip()[:600]}")
            except Exception:
                pass
        out = index + ("\n\n" + "\n\n".join(bodies) if bodies else "")
        return out[:MAX_CHARS] + ("\n... [truncated]" if len(out) > MAX_CHARS else "")
    except Exception as e:
        return f"(skills failed: {e})"


def show_skill(name: str) -> str:
    """Full body of one pack by name (dir or file stem)."""
    want = (name or "").strip().lower()
    for pname, _, f in _packs():
        if pname.lower() == want or f.stem.lower() == want or f.parent.name.lower() == want:
            try:
                _, body = parse_frontmatter(f.read_text(errors="replace"))
                body = body.strip()
                return body[:MAX_BODY_CHARS] + ("\n... [truncated]" if len(body) > MAX_BODY_CHARS else "")
            except Exception as e:
                return f"Error reading skill: {e}"
    known = ", ".join(n for n, _, _ in _packs()[:20])
    return f"Unknown skill '{name}'. Known: {known or '(none)'}"


def list_skills() -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for name, _, f in _packs():
        try:
            out.append((name, f.stat().st_size))
        except Exception:
            pass
    return out


def install_preset(name: str, force: bool = False) -> str:
    """Shallow-clone a preset repo (e.g. superpowers) into SKILLS_DIR."""
    import shutil
    import subprocess

    key = (name or "").strip().lower()
    url = PRESET_REPOS.get(key, "")
    if not url and "://" in (name or ""):
        url, key = name.strip(), "custom"
    if not url:
        return f"Unknown preset '{name}'. Known: {', '.join(PRESET_REPOS)} — or pass a git URL."
    if shutil.which("git") is None:
        return "Error: git not found."
    dest = SKILLS_DIR / key
    if dest.exists():
        if not force:
            return f"{key} already at {dest} — pass --force to re-clone."
        shutil.rmtree(dest, ignore_errors=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["git", "clone", "--depth", "1", url, str(dest)], capture_output=True, text=True, timeout=120, check=True)
    except subprocess.CalledProcessError as e:
        return f"Clone failed: {(e.stderr or '')[:300]}"
    except Exception as e:
        return f"Clone failed: {e}"
    n = len([p for p in dest.glob("**/SKILL.md") if p.is_file()])
    return f"Installed {key} ({n} skills) to {dest}."
