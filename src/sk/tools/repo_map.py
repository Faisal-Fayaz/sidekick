"""Read-only repository map: ``repo_map`` (#400).

The map is a compact, deterministic outline of a repository: root metadata,
language totals, file sizes, and top-level symbols. Python files are parsed
with ``ast``; other source languages use conservative line-anchored regexes.
Everything is read-only and goes through the same path guards, ignore rules,
and symlink policy as search/read.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from ..tokens import CHARS_PER_TOKEN
from .read import _check_read_path, _parse_optional_int
from .search import _file_size, _iter_files, _resolve_root

DEFAULT_REPO_MAP_TOKENS = 1_500
MIN_REPO_MAP_TOKENS = 100
MAX_REPO_MAP_TOKENS = 8_000
MAX_REPO_MAP_FILES = 5_000
MAX_REPO_MAP_NODES = 500
MAX_REPO_MAP_PARSE_BYTES = 500_000
MAX_REPO_MAP_SYMBOLS = 12
MAX_REPO_MAP_LANGUAGES = 8
MAX_REPO_MAP_LANGUAGE_CHARS = 160
MAX_REPO_MAP_FOOTER_CHARS = 180

# Directories that are never useful in a source map and can dominate walk
# time or output. Hidden directories (``.git``, ``.venv``, caches) are already
# skipped by the shared walker.
REPO_MAP_SKIP_DIRS = frozenset(
    {
        "__pycache__",
        "bower_components",
        "build",
        "coverage",
        "dist",
        "env",
        "htmlcov",
        "node_modules",
        "obj",
        "out",
        "Pods",
        "site",
        "target",
        "vendor",
        "venv",
        "virtualenv",
    }
)

_LANGUAGE_BY_SUFFIX = {
    ".bash": "Shell",
    ".c": "C",
    ".cc": "C++",
    ".clj": "Clojure",
    ".cljs": "Clojure",
    ".cmake": "CMake",
    ".cpp": "C++",
    ".cs": "C#",
    ".css": "CSS",
    ".cxx": "C++",
    ".dart": "Dart",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".go": "Go",
    ".h": "C",
    ".hh": "C++",
    ".hpp": "C++",
    ".hs": "Haskell",
    ".htm": "HTML",
    ".html": "HTML",
    ".hxx": "C++",
    ".java": "Java",
    ".jl": "Julia",
    ".js": "JavaScript",
    ".json": "JSON",
    ".jsonc": "JSON",
    ".jsonl": "JSON",
    ".jsx": "JavaScript",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".less": "CSS",
    ".lua": "Lua",
    ".md": "Markdown",
    ".mdx": "Markdown",
    ".mjs": "JavaScript",
    ".nim": "Nim",
    ".php": "PHP",
    ".pl": "Perl",
    ".pm": "Perl",
    ".proto": "Protobuf",
    ".ps1": "PowerShell",
    ".py": "Python",
    ".pyi": "Python",
    ".r": "R",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".sass": "CSS",
    ".scala": "Scala",
    ".scss": "CSS",
    ".sh": "Shell",
    ".sol": "Solidity",
    ".sql": "SQL",
    ".svelte": "Svelte",
    ".swift": "Swift",
    ".toml": "TOML",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".txt": "Text",
    ".vue": "Vue",
    ".xml": "XML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".zig": "Zig",
    ".zsh": "Shell",
}

_LANGUAGE_BY_NAME = {
    "CMakeLists.txt": "CMake",
    "Dockerfile": "Docker",
    "GNUmakefile": "Make",
    "Justfile": "Just",
    "Makefile": "Make",
    "Procfile": "Procfile",
}

# Languages whose top-level definitions are worth extracting with the regex
# fallback. Data formats and markup are deliberately excluded: a "function"
# line in Markdown is prose, not a symbol.
_SOURCE_LANGUAGES = frozenset(
    {
        "C",
        "C#",
        "C++",
        "Clojure",
        "Dart",
        "Elixir",
        "Go",
        "Haskell",
        "Java",
        "JavaScript",
        "Julia",
        "Kotlin",
        "Lua",
        "Nim",
        "PHP",
        "Perl",
        "PowerShell",
        "Python",
        "R",
        "Ruby",
        "Rust",
        "Scala",
        "Shell",
        "Solidity",
        "Swift",
        "TypeScript",
        "Zig",
    }
)

# Patterns are checked in order and names are de-duplicated. They are
# line-anchored so comments and ordinary expressions do not produce symbols.
_CLASS_KEYWORDS = (
    "class",
    "interface",
    "record",
    "struct",
    "trait",
    "enum",
    "object",
    "protocol",
    "module",
    "namespace",
)
_DEFINITION_PREFIX = (
    r"(?:export\s+(?:default\s+)?)?"
    r"(?:pub(?:\([^)]*\))?\s+)?"
    r"(?:public\s+|private\s+|protected\s+|internal\s+|abstract\s+|"
    r"final\s+|sealed\s+|data\s+|open\s+|partial\s+|static\s+|"
    r"annotation\s+|declare\s+)*"
)


def _kind_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(r"^\s*" + _DEFINITION_PREFIX + keyword + r"\s+([A-Za-z_$][\w$]*)")


_SYMBOL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (keyword, _kind_pattern(keyword)) for keyword in _CLASS_KEYWORDS
) + (
    ("type", _kind_pattern("type")),
    (
        "function",
        re.compile(
            r"^\s*(?:export\s+(?:default\s+)?)?"
            r"(?:public\s+|private\s+|protected\s+|internal\s+|static\s+|"
            r"final\s+|abstract\s+|async\s+|suspend\s+|inline\s+|external\s+|"
            r"operator\s+|override\s+)*"
            r"function\s+([A-Za-z_$][\w$]*)"
        ),
    ),
    (
        "fn",
        re.compile(
            r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?"
            r"(?:const\s+)?fn\s+([A-Za-z_]\w*)"
        ),
    ),
    (
        "func",
        re.compile(
            r"^\s*(?:(?:public|private|protected|internal|static|final|abstract|"
            r"override|async|mutating|nonmutating)\s+)*"
            r"func\s+(?:\([^)]*\)\s+)?([A-Za-z_]\w*)"
        ),
    ),
    (
        "def",
        re.compile(
            r"^\s*(?:(?:public|private|protected|static|final|abstract)\s+)*"
            r"def\s+(?:self\.)?([A-Za-z_]\w*[!?=]?)"
        ),
    ),
    (
        "fun",
        re.compile(
            r"^\s*(?:(?:public|private|protected|internal|static|final|abstract|"
            r"override|suspend|inline|operator)\s+)*"
            r"fun\s+([A-Za-z_]\w*)"
        ),
    ),
    (
        "function",
        re.compile(
            r"^\s*(?:export\s+)?(?:const|let|var|static)\s+"
            r"([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
            r"(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"
        ),
    ),
    ("function", re.compile(r"^\s*(?:function\s+)?([A-Za-z_]\w*)\s*\(\)\s*\{")),
    (
        "function",
        re.compile(
            r"^\s*(?:[A-Za-z_][\w:<>*&~]*\s+)+"
            r"(?:\*+\s*)?([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{"
        ),
    ),
)

_SYMBOL_STOPWORDS = frozenset(
    {
        "abstract",
        "catch",
        "char",
        "class",
        "const",
        "def",
        "delete",
        "do",
        "double",
        "else",
        "enum",
        "float",
        "fn",
        "for",
        "func",
        "function",
        "if",
        "int",
        "interface",
        "let",
        "namespace",
        "new",
        "private",
        "protected",
        "public",
        "return",
        "sizeof",
        "static",
        "struct",
        "switch",
        "try",
        "type",
        "using",
        "var",
        "void",
        "while",
        "with",
    }
)


@dataclass(frozen=True)
class _FileEntry:
    path: Path
    rel: str
    size: int
    language: str


def _language_for(path: Path) -> str:
    name = path.name
    if name in _LANGUAGE_BY_NAME:
        return _LANGUAGE_BY_NAME[name]
    return _LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), "Other")


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or singular + "s")


def _read_source(entry: _FileEntry) -> str | None:
    """Read only small, non-binary source files. Never raises."""
    if entry.size > MAX_REPO_MAP_PARSE_BYTES:
        return None
    try:
        with entry.path.open("rb") as handle:
            if b"\x00" in handle.read(8192):
                return None
        return entry.path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _python_symbols(text: str) -> tuple[str, ...]:
    """Top-level classes/functions plus methods of top-level classes."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return ()

    symbols: list[str] = []

    def add_class(node: ast.ClassDef) -> None:
        symbols.append(f"class {node.name}")
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(f"def {child.name}")
            elif isinstance(child, ast.ClassDef):
                add_class(child)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            add_class(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            symbols.append(f"{prefix} {node.name}")
    return tuple(symbols)


def _regex_symbols(text: str) -> tuple[str, ...]:
    symbols: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        for label, pattern in _SYMBOL_PATTERNS:
            match = pattern.match(line)
            if match is None:
                continue
            name = match.group(1)
            if name in _SYMBOL_STOPWORDS or name in seen:
                break
            seen.add(name)
            symbols.append(f"{label} {name}")
            break
    return tuple(symbols)


def _symbols_for(entry: _FileEntry) -> tuple[str, ...]:
    if entry.language not in _SOURCE_LANGUAGES:
        return ()
    text = _read_source(entry)
    if text is None:
        return ()
    if entry.language == "Python":
        return _python_symbols(text)
    return _regex_symbols(text)


def _symbol_summary(entry: _FileEntry) -> str:
    symbols = _symbols_for(entry)
    if not symbols:
        return ""
    shown = symbols[:MAX_REPO_MAP_SYMBOLS]
    summary = "; ".join(shown)
    remaining = len(symbols) - len(shown)
    if remaining:
        summary += f"; +{remaining} more"
    return summary


def _file_line(entry: _FileEntry) -> str:
    line = f"  {entry.rel}  {_human_size(entry.size)}"
    summary = _symbol_summary(entry)
    return f"{line}  {summary}" if summary else line


def _language_line(entries: list[_FileEntry]) -> str:
    totals: dict[str, list[int]] = {}
    for entry in entries:
        bucket = totals.setdefault(entry.language, [0, 0])
        bucket[0] += 1
        bucket[1] += entry.size
    if not totals:
        return "languages: (none)"
    ordered = sorted(totals.items(), key=lambda item: (-item[1][0], item[0]))
    selected = ordered[:MAX_REPO_MAP_LANGUAGES]
    while selected:
        parts = [
            f"{name} {count} {_plural(count, 'file')} ({_human_size(size)})"
            for name, (count, size) in selected
        ]
        remaining = len(ordered) - len(selected)
        if remaining:
            parts.append(f"+{remaining} more")
        line = "languages: " + ", ".join(parts)
        if len(line) <= MAX_REPO_MAP_LANGUAGE_CHARS:
            return line
        selected = selected[:-1]
    line = f"languages: +{len(ordered)} more"
    return line[: MAX_REPO_MAP_LANGUAGE_CHARS - 3].rstrip() + "..."


def _collect_entries(root: Path) -> tuple[list[_FileEntry], bool]:
    """Collect deterministic file metadata, applying guards and caps."""
    if root.is_file():
        size = _file_size(root)
        if size is None:
            return [], False
        return [_FileEntry(root, root.name, size, _language_for(root))], False

    entries: list[_FileEntry] = []
    scan_truncated = False
    for path in _iter_files(root, skip_dirs=REPO_MAP_SKIP_DIRS):
        if isinstance(_check_read_path(str(path)), str):
            continue
        size = _file_size(path)
        if size is None:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if len(entries) >= MAX_REPO_MAP_FILES:
            scan_truncated = True
            break
        entries.append(_FileEntry(path, rel, size, _language_for(path)))
    entries.sort(key=lambda entry: entry.rel)
    return entries, scan_truncated


def _footer(
    shown: int,
    total: int,
    max_tokens: int,
    token_truncated: bool,
    node_truncated: bool,
    scan_truncated: bool,
) -> str | None:
    reasons: list[str] = []
    if token_truncated:
        reasons.append(f"token cap {max_tokens}")
    if node_truncated:
        reasons.append(f"node cap {MAX_REPO_MAP_NODES}")
    if scan_truncated:
        reasons.append(f"scan cap {MAX_REPO_MAP_FILES}")
    if not reasons:
        return None
    suffix = f"; {', '.join(reasons)}" if reasons else ""
    scope = "scanned files" if scan_truncated else "files"
    return f"... [truncated: showing {shown} of {total} {scope}{suffix}]"


def _format_map(
    root: Path,
    entries: list[_FileEntry],
    max_tokens: int,
    scan_truncated: bool,
) -> str:
    budget = max_tokens * CHARS_PER_TOKEN
    total_size = sum(entry.size for entry in entries)
    header = f"{root}: {len(entries)} {_plural(len(entries), 'file')}, {_human_size(total_size)}"
    lines = [header, _language_line(entries), "tree:"]
    used = len("\n".join(lines))

    node_limit = min(len(entries), MAX_REPO_MAP_NODES)
    node_truncated = len(entries) > node_limit
    shown = 0
    if not entries:
        empty = "  (empty)"
        lines.append(empty)
        used += 1 + len(empty)
    else:
        for entry in entries[:node_limit]:
            line = _file_line(entry)
            if used + 1 + len(line) + MAX_REPO_MAP_FOOTER_CHARS > budget:
                break
            lines.append(line)
            used += 1 + len(line)
            shown += 1

    while True:
        footer = _footer(
            shown,
            len(entries),
            max_tokens,
            shown < node_limit,
            node_truncated,
            scan_truncated,
        )
        if footer is None:
            break
        if used + 1 + len(footer) <= budget:
            lines.append(footer)
            break
        if shown > 0:
            removed = lines.pop()
            used -= 1 + len(removed)
            shown -= 1
            continue
        footer = footer[: max(0, budget - used - 1)]
        if footer:
            lines.append(footer)
        break

    output = "\n".join(lines)
    return output[:budget]


def tool_repo_map(path: str = ".", max_tokens: int = DEFAULT_REPO_MAP_TOKENS) -> str:
    """Return a compact repository map with a token/node cap and explicit truncation.

    ``path`` may name a directory or a single regular file. Hidden paths,
    ignored files, symlinks, sensitive roots, and common dependency/build
    directories are excluded. ``max_tokens`` is an approximate cap using the
    shared four-characters-per-token estimator.
    """
    try:
        limit, err = _parse_optional_int(max_tokens, "max_tokens")
        if err:
            return err
        if limit is None:
            limit = DEFAULT_REPO_MAP_TOKENS
        if limit < MIN_REPO_MAP_TOKENS:
            return f"Error: max_tokens must be >= {MIN_REPO_MAP_TOKENS}."
        limit = min(limit, MAX_REPO_MAP_TOKENS)

        root = _resolve_root(str(path or "."))
        if isinstance(root, str):
            return root
        entries, scan_truncated = _collect_entries(root)
        return _format_map(root, entries, limit, scan_truncated)
    except Exception as exc:
        return f"Error: {exc}"
