"""Project memory files: auto-discovered repo conventions (SIDEKICK.md et al)
plus /init scaffolding. No LLM, never raises."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

MEMORY_NAMES = ("SIDEKICK.md", "AGENTS.md", "CLAUDE.md", "GEMINI.md")
DISCOVERY_BUDGET = 8000
MAX_FILE_BYTES = 100_000


def _git_root(start: Path) -> Path | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(start),
        )
        if r.returncode == 0 and (r.stdout or "").strip():
            return Path((r.stdout or "").strip()).resolve()
    except Exception:
        pass
    return None


def discover_memory_files(start: str = "") -> list[Path]:
    """Walk start → git root (start alone outside repos): per directory the
    first hit of SIDEKICK.md/AGENTS.md/CLAUDE.md/GEMINI.md, root-down. Never raises.
    """
    try:
        cur = Path(start or os.getcwd()).expanduser().resolve()
    except Exception:
        return []
    if not cur.is_dir():
        cur = cur.parent
    root = _git_root(cur)
    stop = root if root is not None else cur
    chain: list[Path] = []
    d = cur
    for _ in range(64):
        chain.append(d)
        if d == stop or d == d.parent:
            break
        d = d.parent
    found: list[Path] = []
    for d in reversed(chain):
        for name in MEMORY_NAMES:
            p = d / name
            try:
                if p.is_file() and p.stat().st_size <= MAX_FILE_BYTES:
                    found.append(p)
                    break
            except Exception:
                continue
    return found


def render_memory_files(files: list[Path], budget: int = DISCOVERY_BUDGET) -> str:
    """[abs path] + text per file, budget-bounded. Never raises."""
    parts: list[str] = []
    for p in files:
        try:
            text = p.read_text(errors="replace").strip()[:budget]
        except Exception:
            continue
        if text:
            parts.append(f"[{p}]\n{text}")
            budget -= len(text)
            if budget <= 0:
                break
    return "\n\n".join(parts)


_STACK_MARKERS = (
    ("pyproject.toml", "Python"),
    ("requirements.txt", "Python"),
    ("package.json", "Node"),
    ("Cargo.toml", "Rust"),
    ("go.mod", "Go"),
    ("Gemfile", "Ruby"),
    ("pom.xml", "Java"),
)

_TEST_HINTS = (
    ("pyproject.toml", "pytest"),
    ("package.json", "npm test"),
    ("Cargo.toml", "cargo test"),
    ("go.mod", "go test"),
)


def detect_facts(cwd: str = "") -> dict:
    """Best-effort repo facts for /init scaffolding. Never raises."""
    facts: dict = {"name": "", "stack": [], "test": "", "remote": ""}
    try:
        d = Path(cwd or os.getcwd()).expanduser()
        facts["name"] = d.name
        if not d.is_dir():
            return facts
        for marker, lang in _STACK_MARKERS:
            if (d / marker).is_file() and lang not in facts["stack"]:
                facts["stack"].append(lang)
        for marker, cmd in _TEST_HINTS:
            if (d / marker).is_file():
                facts["test"] = cmd
                break
        if ((d / "tests").is_dir() or (d / "test").is_dir()) and not facts["test"]:
            facts["test"] = "(tests/ dir found — record the runner below)"
        try:
            r = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(d),
            )
            if r.returncode == 0 and (r.stdout or "").strip():
                facts["remote"] = (r.stdout or "").strip()
        except Exception:
            pass
    except Exception:
        pass
    return facts


def scaffold_memory(cwd: str = "") -> str:
    """Write SIDEKICK.md from template + detected facts. Never clobbers. Never raises."""
    try:
        d = Path(cwd or os.getcwd()).expanduser()
        for name in MEMORY_NAMES:
            if (d / name).is_file():
                return f"_already have {(d / name).name} — edit it instead of scaffolding_"
        facts = detect_facts(str(d))
        stack = "\n".join(f"- {s}" for s in facts["stack"]) or "- (record the stack)"
        test = f"- test: `{facts['test']}`" if facts["test"] else "- test: (record the command)"
        remote = f"\nRemote: {facts['remote']}" if facts["remote"] else ""
        body = (
            f"# SIDEKICK.md — repo conventions for sidekick (auto-scaffolded, edit freely)\n"
            f"\nProject: {facts['name'] or d.name}{remote}\n"
            f"\n## Stack\n{stack}\n"
            f"\n## Commands\n{test}\n- lint: (record the command)\n"
            f"\n## Conventions\n- (code style, review rules, things the agent must never do)\n"
        )
        (d / "SIDEKICK.md").write_text(body)
        return "_created SIDEKICK.md with detected facts — fill in Commands/Conventions_"
    except Exception as e:
        return f"_could not scaffold SIDEKICK.md ({e})_"
