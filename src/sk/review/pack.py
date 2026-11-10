"""Editable review policy: built-in defaults plus user, project, and CLI layers.

The review pack is a policy document, not code. It can change rules,
severities, budgets, and the model profile. It cannot change the agent loop,
approvals, audit, or the hard security gates.

Precedence is deliberately boring::

    built-in < user global < project < CLI

Scalars take the highest layer. Lists append unique values. Rules merge by ID,
so a project can tighten one field without copying the whole rule. Project
packs are untrusted by default: a cloned repository must not be able to change
how its own review runs. Set ``--trust-project-rules`` (or the existing
``SIDEKICK_TRUST_REPO=1`` opt-in) to load the project layer.

A malformed pack is reported, never silently ignored. The CLI surfaces the
same list through ``sk review --check-config``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from ..custom_commands import TRUST_REPO_ENV
from ..trust import sanitize
from .diff import DiffModel, SkipReason
from .schema import CATEGORIES, SEVERITIES

SCHEMA_VERSION = 1
PACK_DIR = ".sidekick"
PACK_FILENAME = "review.toml"

DEFAULT_MODEL = ""
DEFAULT_MAX_FINDINGS = 25
DEFAULT_CONTEXT_LINES = 3
DEFAULT_MAX_CHARS = 12_000
DEFAULT_MAX_FILES = 200
DEFAULT_MAX_DIFF_BYTES = 400_000
DEFAULT_FAIL_ON = "never"
ASSESS_MODES = ("auto", "always", "never")
_FAIL_VALUES = (*SEVERITIES, "never")

_TOP_KEYS = frozenset({"schema", "review", "rules"})
_REVIEW_KEYS = frozenset(
    {
        "model",
        "max_findings",
        "context_lines",
        "fail_on",
        "assess",
        "max_chars",
        "max_files",
        "max_diff_bytes",
        "include",
        "ignore",
        "allow_protected_override",
    }
)
_RULE_KEYS = frozenset(
    {"id", "severity", "category", "applies_to", "guidance", "disable", "enabled"}
)
_RULE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*$")


def _rule_id_ok(rule_id: str) -> bool:
    return bool(rule_id) and len(rule_id) <= 80 and _RULE_ID.fullmatch(rule_id) is not None


@dataclass(frozen=True)
class ReviewRule:
    id: str
    severity: str
    category: str
    applies_to: tuple[str, ...] = ("**/*",)
    guidance: str = ""
    protected: bool = False
    source_layer: str = "built-in"


BUILTIN_RULES: tuple[ReviewRule, ...] = (
    ReviewRule(
        id="security/dynamic-eval",
        severity="major",
        category="security",
        applies_to=("**/*.py",),
        guidance=(
            "Flag eval() calls. Evaluating a string as Python code can turn untrusted "
            "data into arbitrary code execution; use a parser or ast.literal_eval for data."
        ),
        protected=True,
    ),
    ReviewRule(
        id="security/dynamic-exec",
        severity="major",
        category="security",
        applies_to=("**/*.py",),
        guidance=(
            "Flag exec() calls. Executing a string as Python code is hard to audit; "
            "use an explicit dispatch table or a normal function call."
        ),
        protected=True,
    ),
    ReviewRule(
        id="security/os-system",
        severity="major",
        category="security",
        applies_to=("**/*.py",),
        guidance=(
            "Flag os.system(). It runs through a shell, so metacharacters in the "
            "argument can execute extra commands. Prefer subprocess.run with argv."
        ),
        protected=True,
    ),
    ReviewRule(
        id="security/subprocess-shell",
        severity="major",
        category="security",
        applies_to=("**/*.py",),
        guidance=(
            "Flag subprocess calls with shell=True. Pass an argv list and keep "
            "shell=False so a crafted argument cannot run another command."
        ),
        protected=True,
    ),
    ReviewRule(
        id="security/unsafe-yaml-load",
        severity="major",
        category="security",
        applies_to=("**/*.py",),
        guidance=(
            "Flag yaml.load() without a safe loader. The default loader can construct "
            "arbitrary Python objects from untrusted YAML; use yaml.safe_load()."
        ),
        protected=True,
    ),
    ReviewRule(
        id="security/tls-verify-disabled",
        severity="major",
        category="security",
        applies_to=("**/*.py",),
        guidance=(
            "Flag verify=False on HTTP/TLS clients and session config. Disabling "
            "certificate verification allows network attackers to impersonate servers."
        ),
        protected=True,
    ),
    ReviewRule(
        id="security/weak-hash",
        severity="major",
        category="security",
        applies_to=("**/*.py",),
        guidance=(
            "Flag MD5 and SHA-1 for security use. Both are collision-broken; use "
            "SHA-256, or a password hash such as scrypt/argon2. Allow an explicit "
            "usedforsecurity=False cache-key exception."
        ),
        protected=True,
    ),
    ReviewRule(
        id="security/sql-injection",
        severity="major",
        category="security",
        applies_to=("**/*.py",),
        guidance=(
            "Flag SQL built with f-strings, % interpolation, concatenation, or "
            ".format(). Use bound parameters: execute(sql, params)."
        ),
        protected=True,
    ),
    ReviewRule(
        id="correctness/mutable-default",
        severity="minor",
        category="correctness",
        applies_to=("**/*.py",),
        guidance=(
            "Flag mutable default arguments such as [] or {}. The container is shared "
            "across calls; default to None and create it inside the function."
        ),
        protected=False,
    ),
    ReviewRule(
        id="correctness/bare-except",
        severity="minor",
        category="correctness",
        applies_to=("**/*.py",),
        guidance=(
            "Flag bare except: handlers. They hide real bugs and swallow "
            "KeyboardInterrupt/SystemExit; catch the specific exceptions you handle."
        ),
        protected=False,
    ),
)

# A protected ID stays protected even if the user layer disables it: a later
# project layer must not be able to re-add the ID as an unprotected rule.
_PROTECTED_IDS = frozenset(r.id for r in BUILTIN_RULES if r.protected)


@dataclass(frozen=True)
class PackLayer:
    name: str
    path: str = ""
    loaded: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ReviewPack:
    schema: int
    model: str
    max_findings: int
    context_lines: int
    fail_on: str
    assess: str
    max_chars: int
    max_files: int
    max_diff_bytes: int
    include: tuple[str, ...]
    ignore: tuple[str, ...]
    rules: tuple[ReviewRule, ...]
    root: str = ""
    layers: tuple[PackLayer, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    hash: str = ""

    def rule_prompt(self) -> str:
        """Human-readable effective rules for the review system prompt."""
        lines: list[str] = []
        for rule in self.rules:
            if not rule.guidance:
                continue
            applies = ", ".join(rule.applies_to)
            guidance = sanitize(rule.guidance, limit=800)
            lines.append(
                f"- {rule.id} [{rule.severity}/{rule.category}] applies to {applies}: {guidance}"
            )
        return "\n".join(lines)

    def check(self) -> list[tuple[str, str]]:
        """Validation issues as (level, message). Errors block review; warnings do not."""
        out: list[tuple[str, str]] = [("error", e) for e in self.errors]
        out.extend(("warning", w) for w in self.warnings)
        out.extend(("warning", w) for w in self._fixture_warnings())
        return out

    def explain(self) -> str:
        """Stable, human-readable description of the effective policy."""
        lines = [
            f"Review pack · schema {self.schema}",
            f"hash: {self.hash}",
            "",
            "Layers:",
        ]
        for layer in self.layers:
            where = layer.path or ("(built-in)" if layer.name == "built-in" else "(no pack file)")
            state = "loaded" if layer.loaded else f"not loaded — {layer.reason}"
            lines.append(f"  {layer.name}: {where} — {state}")
        lines.extend(
            [
                "",
                "Effective settings:",
                f"  model: {self.model or '(configured model)'}",
                f"  max_findings: {self.max_findings}",
                f"  context_lines: {self.context_lines}",
                f"  fail_on: {self.fail_on}",
                f"  assess: {self.assess}",
                f"  max_chars: {self.max_chars}",
                f"  max_files: {self.max_files}",
                f"  max_diff_bytes: {self.max_diff_bytes}",
                f"  include: {list(self.include) or '[]'}",
                f"  ignore: {list(self.ignore) or '[]'}",
                "",
                f"Rules ({len(self.rules)}):",
            ]
        )
        for rule in self.rules:
            flags = " protected" if rule.protected else ""
            lines.append(
                f"  {rule.id} [{rule.severity}/{rule.category}] source={rule.source_layer}{flags}"
            )
            if rule.guidance:
                lines.append(f"    {sanitize(rule.guidance, limit=180)}")
        warnings = (*self.warnings, *self._fixture_warnings())
        if warnings:
            lines.extend(["", "Warnings:"])
            lines.extend(f"  - {w}" for w in warnings)
        if self.errors:
            lines.extend(["", "Errors:"])
            lines.extend(f"  - {e}" for e in self.errors)
        return "\n".join(lines)

    def _fixture_warnings(self) -> list[str]:
        """Best-effort fixture check for built-in rules in a source checkout."""
        if not self.root:
            return []
        root = Path(self.root) / "tests" / "review" / "rules"
        if not root.is_dir():
            return []
        out: list[str] = []
        for rule in self.rules:
            if rule.source_layer != "built-in":
                continue
            d = root / Path(rule.id)
            missing = [
                name
                for name in ("positive.py", "negative.py", "expected.json")
                if not (d / name).is_file()
            ]
            if missing:
                out.append(f"rule {rule.id} has no complete fixture: missing {', '.join(missing)}")
        return out


@dataclass(frozen=True)
class _RulePatch:
    id: str
    fields: dict[str, Any]
    disable: bool = False


@dataclass(frozen=True)
class _LayerData:
    path: Path
    options: dict[str, Any]
    rules: tuple[_RulePatch, ...]
    allow_protected_override: bool = False


def _default_user_path() -> Path:
    from .. import config as config_mod

    return config_mod.CONFIG_DIR / PACK_FILENAME


def _env_trusted() -> bool:
    return os.environ.get(TRUST_REPO_ENV, "").strip() == "1"


def _project_path(cwd: str = "") -> Path | None:
    """``<repo>/.sidekick/review.toml`` for the repository containing cwd.

    The project layer is scoped to the git root, never to an arbitrary parent
    directory. Walking to ``$HOME`` would find the user pack and load it twice.
    """
    try:
        cur = Path(cwd or os.getcwd()).expanduser().resolve()
    except Exception:
        return None
    for parent in (cur, *cur.parents):
        try:
            if not (parent / ".git").exists():
                continue
            candidate = parent / PACK_DIR / PACK_FILENAME
            return candidate if candidate.is_file() else None
        except Exception:
            continue
    return None


def load_review_pack(
    *,
    cwd: str = "",
    trust_project: bool = False,
    overrides: dict[str, Any] | None = None,
    user_path: str | Path | None = None,
    project_path: str | Path | None = None,
) -> ReviewPack:
    """Load and merge the review pack. Never raises for malformed pack content.

    Errors are collected on :class:`ReviewPack`; ``review()`` refuses to run
    with them and ``--check-config`` prints them. ``user_path`` and
    ``project_path`` exist for tests and explicit tooling.
    """
    root = str(Path(cwd or os.getcwd()).expanduser().resolve())
    errors: list[str] = []
    warnings: list[str] = []
    options: dict[str, Any] = {
        "model": DEFAULT_MODEL,
        "max_findings": DEFAULT_MAX_FINDINGS,
        "context_lines": DEFAULT_CONTEXT_LINES,
        "fail_on": DEFAULT_FAIL_ON,
        "assess": "auto",
        "max_chars": DEFAULT_MAX_CHARS,
        "max_files": DEFAULT_MAX_FILES,
        "max_diff_bytes": DEFAULT_MAX_DIFF_BYTES,
        "include": (),
        "ignore": (),
    }
    rules: dict[str, ReviewRule] = {r.id: r for r in BUILTIN_RULES}
    layers: list[PackLayer] = [PackLayer("built-in", loaded=True)]
    allow_protected = False

    user = Path(user_path).expanduser() if user_path is not None else _default_user_path()
    user_data, user_errors, user_warnings = _read_layer(user, "user")
    errors.extend(user_errors)
    warnings.extend(user_warnings)
    if user_data is not None and not user_errors:
        allow_protected = user_data.allow_protected_override
        _apply_layer(options, rules, user_data, "user", allow_protected, errors, warnings)
        layers.append(PackLayer("user", str(user), True))
    elif user_data is not None:
        layers.append(PackLayer("user", str(user), False, "invalid config"))
    elif user.is_file():
        layers.append(PackLayer("user", str(user), False, "unreadable config"))
    else:
        layers.append(PackLayer("user", str(user), False, "not found"))

    project = _project_path(root) if project_path is None else Path(project_path).expanduser()
    trusted = bool(trust_project or _env_trusted())
    if project is not None and project.is_file():
        if not trusted:
            reason = (
                "ignored — project review packs are untrusted; use "
                "--trust-project-rules or SIDEKICK_TRUST_REPO=1"
            )
            layers.append(PackLayer("project", str(project), False, reason))
            warnings.append(f"{project}: {reason}")
        else:
            project_data, project_errors, project_warnings = _read_layer(project, "project")
            errors.extend(project_errors)
            warnings.extend(project_warnings)
            if project_data is not None and not project_errors:
                if project_data.allow_protected_override:
                    warnings.append(
                        f"{project}: allow_protected_override is user-layer only; ignored"
                    )
                _apply_layer(options, rules, project_data, "project", False, errors, warnings)
                layers.append(PackLayer("project", str(project), True))
            elif project_data is not None:
                layers.append(PackLayer("project", str(project), False, "invalid config"))
            else:
                layers.append(PackLayer("project", str(project), False, "unreadable config"))
    else:
        layers.append(PackLayer("project", str(project or ""), False, "not found"))

    cli = dict(overrides or {})
    cli_layer = _cli_layer(cli, errors)
    if cli_layer is not None:
        _apply_layer(options, rules, cli_layer, "cli", False, errors, warnings)
        layers.append(PackLayer("cli", "<command line>", True))

    errors.extend(_validate_options(options))
    errors.extend(_validate_rules(rules.values()))
    effective_rules = tuple(sorted(rules.values(), key=lambda r: r.id))
    effective: dict[str, Any] = {
        "model": str(options["model"]) if isinstance(options["model"], str) else "",
        "max_findings": _int_or(options["max_findings"], DEFAULT_MAX_FINDINGS),
        "context_lines": _int_or(options["context_lines"], DEFAULT_CONTEXT_LINES),
        "fail_on": (
            str(options["fail_on"]) if options["fail_on"] in _FAIL_VALUES else DEFAULT_FAIL_ON
        ),
        "assess": str(options["assess"]) if options["assess"] in ASSESS_MODES else "auto",
        "max_chars": _int_or(options["max_chars"], DEFAULT_MAX_CHARS),
        "max_files": _int_or(options["max_files"], DEFAULT_MAX_FILES),
        "max_diff_bytes": _int_or(options["max_diff_bytes"], DEFAULT_MAX_DIFF_BYTES),
        "include": _patterns(options["include"]),
        "ignore": _patterns(options["ignore"]),
    }
    pack_hash = _pack_hash(effective, effective_rules)
    return ReviewPack(
        schema=SCHEMA_VERSION,
        model=effective["model"],
        max_findings=effective["max_findings"],
        context_lines=effective["context_lines"],
        fail_on=effective["fail_on"],
        assess=effective["assess"],
        max_chars=effective["max_chars"],
        max_files=effective["max_files"],
        max_diff_bytes=effective["max_diff_bytes"],
        include=effective["include"],
        ignore=effective["ignore"],
        rules=effective_rules,
        root=root,
        layers=tuple(layers),
        warnings=tuple(dict.fromkeys(warnings)),
        errors=tuple(dict.fromkeys(errors)),
        hash=pack_hash,
    )


def _int_or(value: Any, default: int) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        return int(value)
    except Exception:
        return default


def _patterns(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _read_layer(path: Path, layer: str) -> tuple[_LayerData | None, list[str], list[str]]:
    if not path.is_file():
        return None, [], []
    try:
        import tomllib

        raw = tomllib.loads(path.read_text(errors="replace"))
    except Exception as e:
        return None, [f"{path}: {e}"], []
    if not isinstance(raw, dict):
        return None, [f"{path}: top level must be a table"], []

    errors: list[str] = []
    warnings: list[str] = []
    for key in raw:
        if key not in _TOP_KEYS:
            errors.append(f"{path}: unknown key {key!r}")

    schema = raw.get("schema")
    if schema is None:
        errors.append(f"{path}: schema is required")
    elif not isinstance(schema, int) or isinstance(schema, bool):
        errors.append(f"{path}: schema must be an integer")
    elif schema != SCHEMA_VERSION:
        errors.append(f"{path}: unsupported schema {schema!r}; expected {SCHEMA_VERSION}")

    options: dict[str, Any] = {}
    review = raw.get("review", {})
    if not isinstance(review, dict):
        errors.append(f"{path}: [review] must be a table")
        review = {}
    else:
        for key in review:
            if key not in _REVIEW_KEYS:
                errors.append(f"{path}: unknown [review] key {key!r}")
        options, review_errors = _parse_review_options(review, path)
        errors.extend(review_errors)

    allow_protected_override = False
    if "allow_protected_override" in review:
        if layer != "user":
            warnings.append(f"{path}: allow_protected_override is user-layer only; ignored")
        else:
            raw_flag = review["allow_protected_override"]
            if isinstance(raw_flag, bool):
                allow_protected_override = raw_flag
            else:
                errors.append(f"{path}: allow_protected_override must be true or false")

    rule_patches: list[_RulePatch] = []
    seen: set[str] = set()
    raw_rules = raw.get("rules", [])
    if not isinstance(raw_rules, list):
        errors.append(f"{path}: [[rules]] must be an array of tables")
        raw_rules = []
    for index, item in enumerate(raw_rules, 1):
        patch, rule_errors = _parse_rule(item, path, index)
        errors.extend(rule_errors)
        if patch is None:
            continue
        if patch.id in seen:
            errors.append(f"{path}: duplicate rule id {patch.id!r}")
            continue
        seen.add(patch.id)
        rule_patches.append(patch)

    return (
        _LayerData(path, options, tuple(rule_patches), allow_protected_override),
        errors,
        warnings,
    )


def _parse_review_options(review: dict[str, Any], path: Path) -> tuple[dict[str, Any], list[str]]:
    out: dict[str, Any] = {}
    errors: list[str] = []

    if "model" in review:
        model = review["model"]
        if not isinstance(model, str):
            errors.append(f"{path}: review.model must be a string")
        else:
            out["model"] = model.strip()[:200]

    for key, low in (
        ("max_findings", 1),
        ("context_lines", 0),
        ("max_chars", 1),
        ("max_files", 1),
        ("max_diff_bytes", 1),
    ):
        if key not in review:
            continue
        try:
            number = int(review[key])
            if isinstance(review[key], bool) or number < low:
                raise ValueError
            out[key] = number
        except Exception:
            errors.append(f"{path}: review.{key} must be an integer >= {low}")

    if "fail_on" in review:
        value = str(review["fail_on"]).strip().lower()
        if value not in _FAIL_VALUES:
            errors.append(f"{path}: review.fail_on must be one of {', '.join(_FAIL_VALUES)}")
        else:
            out["fail_on"] = value

    if "assess" in review:
        value = str(review["assess"]).strip().lower()
        if value not in ASSESS_MODES:
            errors.append(f"{path}: review.assess must be one of {', '.join(ASSESS_MODES)}")
        else:
            out["assess"] = value

    for key in ("include", "ignore"):
        if key not in review:
            continue
        value = review[key]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            errors.append(f"{path}: review.{key} must be an array of glob strings")
            continue
        patterns: list[str] = []
        for pattern in value:
            pattern = pattern.strip()
            error = _glob_error(pattern)
            if error:
                errors.append(f"{path}: review.{key}: {error}")
            else:
                patterns.append(pattern)
        out[key] = tuple(dict.fromkeys(patterns))

    return out, errors


def _parse_rule(item: Any, path: Path, index: int) -> tuple[_RulePatch | None, list[str]]:
    errors: list[str] = []
    if not isinstance(item, dict):
        return None, [f"{path}: rules[{index}] must be a table"]
    for key in item:
        if key not in _RULE_KEYS:
            errors.append(f"{path}: rules[{index}] has unknown key {key!r}")
    rid = str(item.get("id") or "").strip()
    if not rid:
        return None, [*errors, f"{path}: rules[{index}] has no id"]
    if not _rule_id_ok(rid):
        return None, [*errors, f"{path}: invalid rule id {rid!r}"]

    fields: dict[str, Any] = {}
    if "severity" in item:
        severity = str(item["severity"]).strip().lower()
        if severity not in SEVERITIES:
            errors.append(f"{path}: rule {rid!r} severity must be one of {', '.join(SEVERITIES)}")
        else:
            fields["severity"] = severity
    if "category" in item:
        category = str(item["category"]).strip().lower()
        if category not in CATEGORIES:
            errors.append(f"{path}: rule {rid!r} category must be one of {', '.join(CATEGORIES)}")
        else:
            fields["category"] = category
    if "applies_to" in item:
        value = item["applies_to"]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            errors.append(f"{path}: rule {rid!r} applies_to must be an array of glob strings")
        else:
            patterns: list[str] = []
            for pattern in value:
                pattern = pattern.strip()
                error = _glob_error(pattern)
                if error:
                    errors.append(f"{path}: rule {rid!r} applies_to: {error}")
                else:
                    patterns.append(pattern)
            fields["applies_to"] = tuple(dict.fromkeys(patterns))
    if "guidance" in item:
        guidance = item["guidance"]
        if not isinstance(guidance, str):
            errors.append(f"{path}: rule {rid!r} guidance must be a string")
        else:
            fields["guidance"] = guidance.strip()[:4000]

    disable = bool(item.get("disable") is True or item.get("enabled") is False)
    if "disable" in item and not isinstance(item["disable"], bool):
        errors.append(f"{path}: rule {rid!r} disable must be true or false")
    if "enabled" in item and not isinstance(item["enabled"], bool):
        errors.append(f"{path}: rule {rid!r} enabled must be true or false")
    if disable and fields:
        errors.append(f"{path}: rule {rid!r} cannot set fields and disable in the same layer")
    return _RulePatch(rid, fields, disable), errors


def _apply_layer(
    options: dict[str, Any],
    rules: dict[str, ReviewRule],
    layer: _LayerData,
    layer_name: str,
    allow_protected: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    for key, value in layer.options.items():
        if key in ("include", "ignore"):
            # Lists append unique values so a project can add a scope without
            # discarding the user's. Scalars still take the highest layer.
            if not isinstance(value, (list, tuple)):
                options[key] = value  # validation reports the bad type
                continue
            merged = list(options.get(key, ()))
            for item in value:
                if item not in merged:
                    merged.append(item)
            options[key] = tuple(merged)
        else:
            options[key] = value

    for patch in layer.rules:
        existing = rules.get(patch.id)
        if patch.disable:
            if existing is None:
                warnings.append(f"{layer_name}: rule {patch.id!r} not found; disable ignored")
                continue
            if existing.protected and not (layer_name == "user" and allow_protected):
                errors.append(f"{layer_name}: refusing to disable protected rule {patch.id!r}")
                continue
            del rules[patch.id]
            continue

        if existing is not None and existing.protected:
            if layer_name != "user" or not allow_protected:
                errors.append(f"{layer_name}: refusing to override protected rule {patch.id!r}")
                continue

        if existing is None:
            if patch.id in _PROTECTED_IDS:
                errors.append(
                    f"{layer_name}: refusing to define disabled protected rule {patch.id!r}"
                )
                continue
            if layer_name == "cli":
                warnings.append(f"cli: rule {patch.id!r} not found; override ignored")
                continue
            fields = dict(patch.fields)
            rules[patch.id] = ReviewRule(
                id=patch.id,
                severity=str(fields.pop("severity", "minor")),
                category=str(fields.pop("category", "correctness")),
                applies_to=tuple(fields.pop("applies_to", ("**/*",))),
                guidance=str(fields.pop("guidance", "")),
                protected=False,
                source_layer=layer_name,
            )
            continue

        fields = dict(patch.fields)
        rules[patch.id] = dataclasses.replace(
            existing,
            **fields,
            source_layer=layer_name,
        )


def _cli_layer(overrides: dict[str, Any], errors: list[str]) -> _LayerData | None:
    options: dict[str, Any] = {}
    for key in (
        "model",
        "max_findings",
        "context_lines",
        "fail_on",
        "assess",
        "max_chars",
        "max_files",
        "max_diff_bytes",
        "include",
        "ignore",
    ):
        if key in overrides and overrides[key] is not None:
            options[key] = overrides[key]

    patches: dict[str, _RulePatch] = {}
    for rid in overrides.get("disable_rules") or ():
        rid = str(rid).strip()
        if rid:
            patches[rid] = _RulePatch(rid, {}, True)
    for raw in overrides.get("severity_overrides") or ():
        text = str(raw).strip()
        if "=" not in text:
            errors.append(f"--severity expects RULE=LEVEL, got {text!r}")
            continue
        rid, severity = (part.strip() for part in text.split("=", 1))
        if not rid or not severity:
            errors.append(f"--severity expects RULE=LEVEL, got {text!r}")
            continue
        if not _rule_id_ok(rid):
            errors.append(f"--severity has an invalid rule id {rid!r}")
            continue
        level = severity.lower()
        if level not in SEVERITIES:
            errors.append(f"--severity level must be one of {', '.join(SEVERITIES)}")
            continue
        patches[rid] = _RulePatch(rid, {"severity": level})

    if not options and not patches:
        return None
    return _LayerData(Path("<command line>"), options, tuple(patches.values()))


def _validate_rules(rules: Iterable[ReviewRule]) -> list[str]:
    errors: list[str] = []
    for rule in rules:
        if not _rule_id_ok(rule.id):
            errors.append(f"invalid rule id {rule.id!r}")
        if rule.severity not in SEVERITIES:
            errors.append(f"rule {rule.id!r} has invalid severity {rule.severity!r}")
        if rule.category not in CATEGORIES:
            errors.append(f"rule {rule.id!r} has invalid category {rule.category!r}")
        for pattern in rule.applies_to:
            error = _glob_error(pattern)
            if error:
                errors.append(f"rule {rule.id!r} applies_to: {error}")
    return errors


def _validate_options(options: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    checks = (
        ("max_findings", 1),
        ("context_lines", 0),
        ("max_chars", 1),
        ("max_files", 1),
        ("max_diff_bytes", 1),
    )
    for key, low in checks:
        try:
            value = int(options[key])
            if isinstance(options[key], bool) or value < low:
                raise ValueError
            options[key] = value
        except Exception:
            errors.append(f"effective {key} must be an integer >= {low}")
    if not isinstance(options.get("model"), str):
        errors.append("effective model must be a string")
    if options.get("fail_on") not in _FAIL_VALUES:
        errors.append(f"effective fail_on must be one of {', '.join(_FAIL_VALUES)}")
    if options.get("assess") not in ASSESS_MODES:
        errors.append(f"effective assess must be one of {', '.join(ASSESS_MODES)}")
    for key in ("include", "ignore"):
        value = options.get(key, ())
        if not isinstance(value, (list, tuple)):
            errors.append(f"effective {key} must be a list of glob strings")
            continue
        for pattern in value:
            if not isinstance(pattern, str):
                errors.append(f"effective {key} must contain only glob strings")
                continue
            error = _glob_error(pattern)
            if error:
                errors.append(f"effective {key}: {error}")
    return errors


def _glob_error(pattern: str) -> str:
    if not pattern:
        return "empty glob"
    if "\x00" in pattern:
        return "glob contains a NUL byte"
    normalized = pattern.replace("\\", "/")
    if normalized.startswith("/"):
        return f"absolute glob {pattern!r} is not allowed"
    if any(part == ".." for part in normalized.split("/")):
        return f"glob {pattern!r} may not traverse with '..'"
    try:
        re.compile(_glob_source(normalized))
    except (ValueError, re.error) as e:
        return f"invalid glob {pattern!r}: {e}"
    return ""


def _relative(path: str) -> str:
    """Normalise a repo-relative path without eating dotfile names."""
    out = str(path or "").replace("\\", "/")
    while out.startswith("./"):
        out = out[2:]
    return out.lstrip("/")


def _glob_source(pattern: str) -> str:
    """Translate a review-pack glob to a regex.

    ``**/`` matches zero or more directories, ``**`` matches anything, and a
    single ``*``/``?`` never crosses a path separator. ``[]`` classes are
    supported; an unterminated class is an error rather than a surprise.
    """
    parts: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            parts.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            parts.append(".*")
            i += 2
            continue
        char = pattern[i]
        if char == "*":
            parts.append("[^/]*")
            i += 1
            continue
        if char == "?":
            parts.append("[^/]")
            i += 1
            continue
        if char == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                raise ValueError("unterminated character class")
            body = pattern[i + 1 : end]
            if not body:
                raise ValueError("empty character class")
            if body[0] in ("!", "^"):
                body = "^" + body[1:]
            parts.append(f"[{body}]")
            i = end + 1
            continue
        parts.append(re.escape(char))
        i += 1
    return "^" + "".join(parts) + "$"


@lru_cache(maxsize=512)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    return re.compile(_glob_source(pattern))


def glob_match(path: str, pattern: str) -> bool:
    """Match a repo-relative path against a review-pack glob.

    ``**/`` may match zero directories, so ``**/generated/**`` also catches a
    top-level ``generated/`` directory. A pattern with no slash matches the
    basename, like ``.gitignore``.
    """
    p = _relative(path)
    pat = _relative(pattern)
    if not p or not pat:
        return False
    target = _basename(p) if "/" not in pat else p
    try:
        return _glob_regex(pat).match(target) is not None
    except (ValueError, re.error):
        return False


def _basename(path: str) -> str:
    """Basename helper that does not depend on the host path separator."""
    return path.rsplit("/", 1)[-1]


def rule_applies(rule: ReviewRule, path: str) -> bool:
    return any(glob_match(path, pattern) for pattern in rule.applies_to)


def filter_diff(diff: DiffModel, include: Iterable[str], ignore: Iterable[str]) -> DiffModel:
    """Apply review-pack include/ignore globs and keep skipped coverage honest."""
    include = tuple(include)
    ignore = tuple(ignore)
    if not include and not ignore:
        return diff
    kept = []
    skipped = list(diff.skipped)
    for fd in diff.files:
        if include and not any(glob_match(fd.path, pattern) for pattern in include):
            skipped.append(SkipReason(fd.path, "excluded by review pack include"))
            continue
        if ignore and any(glob_match(fd.path, pattern) for pattern in ignore):
            skipped.append(SkipReason(fd.path, "excluded by review pack ignore"))
            continue
        kept.append(fd)
    changed = sum(len(f.changed_new_lines()) for f in kept)
    return dataclasses.replace(
        diff,
        files=tuple(kept),
        skipped=tuple(skipped),
        changed_lines=changed,
    )


def _pack_hash(options: dict[str, Any], rules: Iterable[ReviewRule]) -> str:
    payload = {
        "schema": SCHEMA_VERSION,
        "model": options["model"],
        "max_findings": options["max_findings"],
        "context_lines": options["context_lines"],
        "fail_on": options["fail_on"],
        "assess": options["assess"],
        "max_chars": options["max_chars"],
        "max_files": options["max_files"],
        "max_diff_bytes": options["max_diff_bytes"],
        "include": list(options["include"]),
        "ignore": list(options["ignore"]),
        "rules": [
            {
                "id": rule.id,
                "severity": rule.severity,
                "category": rule.category,
                "applies_to": list(rule.applies_to),
                "guidance": rule.guidance,
                "protected": rule.protected,
            }
            for rule in sorted(rules, key=lambda r: r.id)
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
