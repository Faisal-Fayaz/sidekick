"""A docs co-change guard: propose updates when code removes a documented name (#521).

The guard reads the previous version of a changed file from git, extracts the
public symbols / config keys / CLI commands it had, and looks for those names
in `README.md` and `docs/*.md`. It **reports**, never edits. Without git it can
still check explicit paths by looking for documented names absent from the
current source (best effort).
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
NAME_RE = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_.-]*)`")


def _git(*args: str, cwd: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def changed_paths(since: str = "", paths: list[str] | None = None, *, cwd: str = "") -> list[str]:
    """Changed source paths from git, or from an explicit `--paths` list."""
    if paths:
        return sorted({str(p).strip() for p in paths if str(p).strip()})
    root = cwd or str(ROOT)
    ref = since.strip() or "HEAD"
    out = _git("diff", "--name-only", ref, "--", "src", cwd=root)
    untracked = _git("ls-files", "--others", "--exclude-standard", "--", "src", cwd=root)
    found = {
        line.strip()
        for line in (out + "\n" + untracked).splitlines()
        if line.strip().endswith(".py")
    }
    return sorted(found)


def public_symbols(text: str) -> set[str]:
    """Top-level functions/classes not starting with `_`."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                out.add(node.name)
    return out


def config_keys(text: str) -> set[str]:
    """Literal keys in the module-level DEFAULTS dict, if one exists."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    for node in tree.body:
        targets = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if (
                isinstance(target, ast.Name)
                and target.id == "DEFAULTS"
                and isinstance(value, ast.Dict)
            ):
                return {
                    k.value
                    for k in value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                }
    return set()


def cli_commands(text: str) -> set[str]:
    """Names registered with @app.command(...) or @app.command in a CLI module."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            func = deco.func
            name_attr = getattr(func, "attr", "")
            if name_attr != "command":
                continue
            if deco.args and isinstance(deco.args[0], ast.Constant):
                out.add(str(deco.args[0].value))
            else:
                out.add(node.name)
    return out


def _names(text: str) -> set[str]:
    return public_symbols(text) | config_keys(text) | cli_commands(text)


def old_source(path: str, since: str, *, cwd: str) -> str:
    ref = since.strip() or "HEAD"
    return _git("show", f"{ref}:{path}", cwd=cwd)


def doc_files(root: Path) -> list[Path]:
    out = [root / "README.md"]
    docs = root / "docs"
    if docs.is_dir():
        out += sorted(docs.glob("*.md"))
    contributing = root / "CONTRIBUTING.md"
    if contributing.is_file():
        out.append(contributing)
    return [p for p in out if p.is_file()]


def check(
    *,
    since: str = "",
    paths: list[str] | None = None,
    cwd: str = "",
) -> dict:
    root = Path(cwd or ROOT)
    changed = changed_paths(since, paths, cwd=str(root))
    unavailable: list[str] = []
    removed: set[str] = set()
    for rel in changed:
        current_file = root / rel
        if not current_file.is_file():
            continue
        current = current_file.read_text(errors="replace")
        previous = old_source(rel, since, cwd=str(root)) if not paths else ""
        if previous:
            removed |= _names(previous) - _names(current)
        elif not paths:
            unavailable.append(rel)
        else:
            # No git baseline: treat a documented identifier that appears
            # nowhere in the current source as a best-effort stale candidate.
            # Comparing against the whole tree (not just this file) keeps the
            # fallback from flagging symbols that merely live elsewhere.
            documented = {
                token
                for path in doc_files(root)
                for token in NAME_RE.findall(path.read_text(errors="replace"))
                if "_" in token or (token[:1].isupper() and len(token) > 3)
            }
            everywhere: set[str] = set()
            for source in (root / "src").rglob("*.py"):
                everywhere |= _names(source.read_text(errors="replace"))
            removed |= documented - everywhere

    findings: list[dict] = []
    if removed:
        for path in doc_files(root):
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                for token in NAME_RE.findall(line):
                    if token in removed:
                        findings.append(
                            {
                                "doc": str(path.relative_to(root)),
                                "line": number,
                                "name": token,
                                "text": line.strip()[:200],
                            }
                        )
    return {
        "schema": 1,
        "changed": changed,
        "removed": sorted(removed),
        "unavailable": sorted(unavailable),
        "findings": findings,
        "ok": not findings,
    }


def render(report: dict) -> str:
    if report["ok"]:
        return f"docs guard: {len(report['changed'])} file(s) changed, no stale references."
    lines = [
        f"docs guard: {len(report['findings'])} stale reference(s) in docs:",
    ]
    for item in report["findings"]:
        lines.append(f"  {item['doc']}:{item['line']} — `{item['name']}`")
    lines.append("Propose a docs update in the same PR; nothing was edited.")
    return "\n".join(lines)
