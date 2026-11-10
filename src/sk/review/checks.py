"""Deterministic review checks that do not need a model.

These are deliberately small and low-noise. The model can miss obvious
security footguns; a check finding still has to pass the same evidence
validation as a model finding.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import Iterable, Mapping

from .diff import DiffModel, FileDiff
from .pack import ReviewRule, rule_applies
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


_SAFE_YAML_LOADERS = frozenset({"SafeLoader", "CSafeLoader", "BaseLoader", "CBaseLoader"})
_WEAK_HASHES = frozenset({"md5", "sha1"})
_MUTABLE_FACTORIES = frozenset({"list", "dict", "set", "defaultdict"})
_SQL_CALLS = frozenset({"execute", "executemany", "executescript"})


def _is_constant(node: ast.AST, value: object) -> bool:
    return (
        isinstance(node, ast.Constant) and type(node.value) is type(value) and node.value == value
    )


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _is_safe_yaml_loader(node: ast.AST) -> bool:
    return _name_of(node) in _SAFE_YAML_LOADERS


def _yaml_is_safe(node: ast.Call) -> bool:
    """A yaml.load() call is safe only when a known safe loader is explicit."""
    for kw in node.keywords:
        if kw.arg == "Loader" or kw.arg == "loader":
            return _is_safe_yaml_loader(kw.value)
    if len(node.args) > 1:
        return _is_safe_yaml_loader(node.args[1])
    return False


def _hash_is_explicitly_non_security(node: ast.Call) -> bool:
    return any(
        kw.arg == "usedforsecurity" and _is_constant(kw.value, False) for kw in node.keywords
    )


def _sql_is_dynamic(node: ast.AST) -> bool:
    """True for SQL assembled by interpolation/formatting/concatenation."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "format":
            return isinstance(node.func.value, ast.Constant) and isinstance(
                node.func.value.value, str
            )
    return False


def _mutable_default(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _MUTABLE_FACTORIES
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

    def emit(rule_id: str, line: int, problem: str, impact: str, suggestion: str) -> None:
        out.append(
            _finding(
                rule_id=rule_id,
                category="security" if rule_id.startswith("security/") else "correctness",
                severity="major" if rule_id.startswith("security/") else "minor",
                path=fd.path,
                line=line,
                evidence=_line_text(path, line),
                problem=problem,
                impact=impact,
                suggestion=suggestion,
            )
        )

    def mutable_defaults(func: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        defaults = [*func.args.defaults, *(d for d in func.args.kw_defaults if d is not None)]
        for default in defaults:
            line = int(getattr(default, "lineno", 0) or 0)
            if line not in changed or not _mutable_default(default):
                continue
            emit(
                "correctness/mutable-default",
                line,
                "A mutable default argument is shared between calls.",
                "One call mutates state that later calls observe.",
                "Default to `None` and create the container inside the function.",
            )

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mutable_defaults(node)
        line = int(getattr(node, "lineno", 0) or 0)
        if line not in changed:
            continue
        evidence = _line_text(path, line)

        if isinstance(node, ast.ExceptHandler) and node.type is None:
            emit(
                "correctness/bare-except",
                line,
                "A bare `except:` catches every exception.",
                "It hides real bugs and swallows KeyboardInterrupt/SystemExit.",
                "Catch the specific exceptions the code can handle.",
            )

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

        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = getattr(node, "value", None)
            if (
                value is not None
                and _is_constant(value, False)
                and any(isinstance(t, ast.Attribute) and t.attr == "verify" for t in targets)
            ):
                emit(
                    "security/tls-verify-disabled",
                    line,
                    "TLS certificate verification is disabled.",
                    "A network attacker can impersonate the server and read or alter traffic.",
                    "Keep verification enabled; fix the trust store or pin a CA instead.",
                )

        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "verify" and _is_constant(kw.value, False):
                emit(
                    "security/tls-verify-disabled",
                    line,
                    "TLS certificate verification is disabled.",
                    "A network attacker can impersonate the server and read or alter traffic.",
                    "Keep verification enabled; fix the trust store or pin a CA instead.",
                )
                break

        if not isinstance(node.func, ast.Attribute):
            continue
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
                if kw.arg == "shell" and _is_constant(kw.value, True):
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

        if root_name == "yaml":
            unsafe = attr == "unsafe_load" or (
                attr in ("load", "load_all") and not _yaml_is_safe(node)
            )
            if unsafe:
                emit(
                    "security/unsafe-yaml-load",
                    line,
                    "`yaml.load()` can construct arbitrary Python objects.",
                    "Untrusted YAML can trigger code execution through unsafe tags.",
                    "Use `yaml.safe_load()` or pass `Loader=SafeLoader`.",
                )

        weak = False
        if root_name == "hashlib" and attr in _WEAK_HASHES:
            weak = True
        elif root_name == "hashlib" and attr == "new" and node.args:
            first = node.args[0]
            weak = _is_constant(first, "md5") or _is_constant(first, "sha1")
        if weak and not _hash_is_explicitly_non_security(node):
            emit(
                "security/weak-hash",
                line,
                "MD5 or SHA-1 is used for hashing.",
                "Both are collision-broken and unsuitable for signatures, passwords, or integrity.",
                "Use SHA-256, or a password hash such as scrypt or argon2.",
            )

        if attr in _SQL_CALLS and node.args and _sql_is_dynamic(node.args[0]):
            emit(
                "security/sql-injection",
                line,
                "SQL is built with string interpolation or concatenation.",
                "Untrusted input can change the query and read or modify arbitrary rows.",
                "Use bound parameters: `execute(sql, params)`.",
            )
    return out


def deterministic_findings(
    diff: DiffModel,
    rules: Iterable[ReviewRule] | Mapping[str, ReviewRule] | None = None,
) -> list[Finding]:
    """Run all non-model checks under the effective review-pack rules.

    Disabled rules disappear; overridden severities and categories are applied
    here rather than at report time so every downstream consumer (JSON, SARIF,
    fail-on) sees one canonical finding.
    """
    if rules is None:
        from .pack import BUILTIN_RULES

        rule_map: Mapping[str, ReviewRule] = {r.id: r for r in BUILTIN_RULES}
    elif isinstance(rules, Mapping):
        rule_map = rules
    else:
        rule_map = {r.id: r for r in rules}

    out: list[Finding] = []
    for fd in diff.files:
        if fd.binary or fd.skipped:
            continue
        try:
            findings = _python_checks(fd, diff.repo_root)
        except Exception:
            continue
        for finding in findings:
            rule = rule_map.get(finding.rule_id)
            if rule is None or not rule_applies(rule, fd.path):
                continue
            out.append(
                dataclasses.replace(
                    finding,
                    severity=rule.severity,
                    category=rule.category,
                )
            )
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
