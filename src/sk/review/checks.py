"""Deterministic review checks that do not need a model.

These are deliberately small and low-noise. The model can miss obvious
security footguns; a check finding still has to pass the same evidence
validation as a model finding.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

from .diff import DiffModel, FileDiff
from .schema import Finding


def _line_text(path: Path, line: int) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
        if 1 <= line <= len(lines):
            return lines[line - 1].strip()[:240]
    except Exception:
        pass
    return ""


def _finding(
    *,
    rule_id: str,
    category: str,
    severity: str,
    path: str,
    line: int,
    evidence: str,
    problem: str,
    impact: str,
    suggestion: str,
    confidence: str = "high",
) -> Finding:
    return Finding(
        id="",
        severity=severity,
        category=category,
        file=path,
        line=line,
        end_line=None,
        evidence=evidence,
        problem=problem,
        impact=impact,
        suggestion=suggestion,
        confidence=confidence,
        rule_id=rule_id,
        source="check",
    )


def _python_checks(fd: FileDiff, repo_root: str) -> list[Finding]:
    if not fd.path.endswith(".py"):
        return []
    path = Path(repo_root) / fd.path
    try:
        source = path.read_text(errors="replace")
        tree = ast.parse(source)
    except Exception:
        return []
    changed = fd.changed_new_lines()
    out: list[Finding] = []
    for node in ast.walk(tree):
        line = int(getattr(node, "lineno", 0) or 0)
        if line not in changed:
            continue
        evidence = _line_text(path, line)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in ("eval", "exec"):
                out.append(
                    _finding(
                        rule_id=f"security/dynamic-{name}",
                        category="security",
                        severity="major",
                        path=fd.path,
                        line=line,
                        evidence=evidence,
                        problem=f"`{name}()` executes a string as code.",
                        impact="Untrusted input can become arbitrary code execution.",
                        suggestion="Use a parser or an explicit dispatch table instead.",
                    )
                )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            root = node.func.value
            root_name = root.id if isinstance(root, ast.Name) else ""
            if attr == "system" and root_name == "os":
                out.append(
                    _finding(
                        rule_id="security/os-system",
                        category="security",
                        severity="major",
                        path=fd.path,
                        line=line,
                        evidence=evidence,
                        problem="`os.system()` runs a command through the shell.",
                        impact="Shell metacharacters in the argument can execute extra commands.",
                        suggestion="Use `subprocess.run([...], shell=False)` with argv.",
                    )
                )
            if attr in ("run", "call", "check_call", "check_output", "Popen"):
                for kw in node.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        out.append(
                            _finding(
                                rule_id="security/subprocess-shell",
                                category="security",
                                severity="major",
                                path=fd.path,
                                line=line,
                                evidence=evidence,
                                problem="`subprocess` is called with `shell=True`.",
                                impact="A crafted argument can run additional shell commands.",
                                suggestion="Pass an argv list and keep `shell=False`.",
                            )
                        )
                        break
    return out


def deterministic_findings(diff: DiffModel) -> list[Finding]:
    """Run all non-model checks. Never raises on a malformed file."""
    out: list[Finding] = []
    for fd in diff.files:
        if fd.binary or fd.skipped:
            continue
        try:
            out.extend(_python_checks(fd, diff.repo_root))
        except Exception:
            continue
    return _dedupe(out)


def _dedupe(findings: Iterable[Finding]) -> list[Finding]:
    seen: set[tuple[str, int, str]] = set()
    out: list[Finding] = []
    for f in findings:
        key = (f.file, f.line, f.rule_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out
