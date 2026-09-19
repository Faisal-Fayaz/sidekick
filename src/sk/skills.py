"""Skills: markdown packs in ~/.sidekick/skills/*.md auto-loaded into prompt.

Format: any markdown. First `# heading` is the title. Keep each <2KB.
Reserved names: none. Cap: 10 files, 1500 chars total injected.
"""

from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path.home() / ".sidekick" / "skills"
MAX_FILES = 10
MAX_CHARS = 1500

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


def load_skills() -> str:
    try:
        ensure_defaults()
        files = sorted(SKILLS_DIR.glob("*.md"))[:MAX_FILES]
        chunks: list[str] = []
        for f in files:
            try:
                if f.stat().st_size > 10_000:
                    continue
                text = f.read_text(errors="replace").strip()
                if text:
                    chunks.append(f"[{f.stem}]\n{text[:600]}")
            except Exception:
                pass
        out = "\n\n".join(chunks)
        return out[:MAX_CHARS] + ("\n... [truncated]" if len(out) > MAX_CHARS else "") if out else "(no skills — add ~/.sidekick/skills/*.md)"
    except Exception as e:
        return f"(skills failed: {e})"


def list_skills() -> list[tuple[str, int]]:
    ensure_defaults()
    out: list[tuple[str, int]] = []
    for f in sorted(SKILLS_DIR.glob("*.md")):
        try:
            out.append((f.stem, f.stat().st_size))
        except Exception:
            pass
    return out
