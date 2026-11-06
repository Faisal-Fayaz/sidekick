"""Read-only code search: ``search_code`` and ``glob_files`` (#398).

Search uses ``rg`` when available, then ``grep``, then a Python walker. All
three paths use the same read-path guards, ignore rules, symlink policy, and
result caps. The Python walker is deliberately the semantic reference: the
external tools are accelerators, and their output is filtered through the same
checks before it reaches the model.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable, Iterator
from functools import lru_cache
from pathlib import Path, PurePosixPath

from ..procutil import run_bounded
from .read import _check_read_path, _parse_optional_int

Match = tuple[str, int, str]

MAX_SEARCH_RESULTS = 50
MAX_SEARCH_RESULTS_HARD = 200
MAX_GLOB_RESULTS = 200
MAX_SEARCH_FILE_BYTES = 5_000_000
MAX_SNIPPET_CHARS = 200
MAX_IGNORE_RULES = 2_000
SEARCH_TIMEOUT = 10
GREP_BATCH = 200


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate a git-style glob (``*``, ``?``, ``**``) to a regex.

    ``*`` and ``?`` never cross ``/``; ``**/`` can match zero or more leading
    directories, so ``**/*.py`` also matches ``a.py`` at the root.
    """
    out = ["^"]
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                i += 2
                if i < n and pattern[i] == "/":
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
            else:
                out.append("[^/]*")
                i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[":
            end = pattern.find("]", i + 1)
            if end < 0:
                out.append(r"\[")
                i += 1
            else:
                cls = pattern[i : end + 1]
                if cls.startswith("[!"):
                    cls = "[^" + cls[2:]
                out.append(cls)
                i = end + 1
        else:
            out.append(re.escape(ch))
            i += 1
    out.append("$")
    try:
        return re.compile("".join(out))
    except re.error:
        return re.compile(r"^$")


def _glob_match(rel: str, pattern: str, *, anchored: bool = False) -> bool:
    """Match a POSIX relative path. Patterns without ``/`` match the basename."""
    pat = pattern.strip().replace("\\", "/")
    if pat.startswith("./"):
        pat = pat[2:]
    if pat.startswith("/"):
        pat = pat[1:]
        anchored = True
    if not pat:
        return False
    target = rel if anchored or "/" in pat else PurePosixPath(rel).name
    return bool(_glob_regex(pat).match(target))


def _glob_filter(rel: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    if pattern.startswith("!"):
        return not _glob_match(rel, pattern[1:])
    return _glob_match(rel, pattern)


def _ignore_rule_matches(rel: str, pattern: str, *, dir_only: bool, is_dir: bool) -> bool:
    """Check one .gitignore rule against a path and each of its parents."""
    anchored = pattern.startswith("/")
    if anchored:
        pattern = pattern[1:]
    if not pattern:
        return False
    parts = rel.split("/")
    for index in range(1, len(parts) + 1):
        candidate = "/".join(parts[:index])
        candidate_is_dir = index < len(parts) or is_dir
        if dir_only and not candidate_is_dir:
            continue
        if _glob_match(candidate, pattern, anchored=anchored):
            return True
    return False


class _IgnoreMatcher:
    """Lazy .gitignore matcher for a git root (or the requested root)."""

    def __init__(self, base: Path):
        self.base = base
        self._cache: dict[Path, list[tuple[str, bool, bool]]] = {}

    def _rules(self, directory: Path) -> list[tuple[str, bool, bool]]:
        cached = self._cache.get(directory)
        if cached is not None:
            return cached
        rules: list[tuple[str, bool, bool]] = []
        path = directory / ".gitignore"
        try:
            if path.is_file():
                text = path.read_text(errors="replace")[:100_000]
                for raw in text.splitlines()[:MAX_IGNORE_RULES]:
                    line = raw.rstrip()
                    if not line or line.startswith("#"):
                        continue
                    negated = line.startswith("!")
                    if negated:
                        line = line[1:]
                    elif line.startswith("\\!"):
                        line = line[1:]
                    elif line.startswith("\\#"):
                        line = line[1:]
                    if not line:
                        continue
                    dir_only = line.endswith("/")
                    if dir_only:
                        line = line[:-1]
                    if line:
                        rules.append((line, negated, dir_only))
        except Exception:
            rules = []
        self._cache[directory] = rules
        return rules

    def ignored(self, path: Path, is_dir: bool) -> bool:
        try:
            resolved = path.resolve()
            rel = resolved.relative_to(self.base)
        except Exception:
            return False
        parts = rel.parts
        if not parts:
            return False
        ignored = False
        directory = self.base
        directories = [directory]
        for part in parts[:-1]:
            directory = directory / part
            directories.append(directory)
        for directory in directories:
            try:
                rel_to_dir = resolved.relative_to(directory).as_posix()
            except Exception:
                continue
            for pattern, negated, dir_only in self._rules(directory):
                if _ignore_rule_matches(rel_to_dir, pattern, dir_only=dir_only, is_dir=is_dir):
                    ignored = not negated
        return ignored


def _ignore_base(root: Path) -> Path:
    """Use the enclosing git root when there is one, else the requested root."""
    directory = root
    for _ in range(64):
        try:
            if (directory / ".git").exists():
                return directory
        except Exception:
            pass
        if directory.parent == directory:
            break
        directory = directory.parent
    return root


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except Exception:
        return None


def _iter_files(
    root: Path, pattern: str | None = None, max_bytes: int | None = None
) -> Iterator[Path]:
    """Yield regular, non-hidden, non-symlink files under root.

    Traversal is lexicographic so capped glob results remain deterministic.
    Ignore rules are applied relative to the enclosing git root.
    """
    matcher = _IgnoreMatcher(_ignore_base(root))

    def walk(directory: Path) -> Iterator[Path]:
        try:
            with os.scandir(directory) as entries:
                ordered = sorted(entries, key=lambda entry: entry.name)
        except Exception:
            return
        for entry in ordered:
            try:
                name = entry.name
                if name.startswith(".") or entry.is_symlink():
                    continue
                path = directory / name
                is_dir = entry.is_dir(follow_symlinks=False)
                if matcher.ignored(path, is_dir):
                    continue
                if is_dir:
                    yield from walk(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                rel = path.relative_to(root).as_posix()
                if not _glob_filter(rel, pattern):
                    continue
                if max_bytes is not None:
                    size = _file_size(path)
                    if size is None or size > max_bytes:
                        continue
                yield path
            except Exception:
                continue

    yield from walk(root)


def _candidate_files(root: Path, pattern: str | None, max_bytes: int | None) -> Iterator[Path]:
    if root.is_file():
        if not _glob_filter(root.name, pattern):
            return
        if max_bytes is not None:
            size = _file_size(root)
            if size is None or size > max_bytes:
                return
        yield root
        return
    yield from _iter_files(root, pattern, max_bytes)


def _snippet(line: str) -> str:
    text = " ".join(line.strip().split())
    if len(text) > MAX_SNIPPET_CHARS:
        text = text[: MAX_SNIPPET_CHARS - 3].rstrip() + "..."
    return text


def _keep_path_factory(root: Path) -> Callable[[Path], bool]:
    matcher = _IgnoreMatcher(_ignore_base(root)) if root.is_dir() else None

    def keep(path: Path) -> bool:
        try:
            if matcher is not None and matcher.ignored(path, path.is_dir()):
                return False
            return not isinstance(_check_read_path(str(path)), str)
        except Exception:
            return False

    return keep


def _run_search(
    argv: list[str], timeout: float = SEARCH_TIMEOUT
) -> tuple[str, str, int, bool, bool]:
    result = run_bounded(argv, timeout=timeout)
    return result.stdout, result.stderr, result.returncode, result.truncated, result.timed_out


def _rg_search(
    root: Path,
    pattern: str,
    glob: str | None,
    limit: int,
    keep: Callable[[Path], bool],
    executable: str,
) -> tuple[list[Match], str, bool]:
    argv = [
        executable,
        "--no-config",
        "--json",
        "--color",
        "never",
        "--max-count",
        str(limit + 1),
        "--max-filesize",
        str(MAX_SEARCH_FILE_BYTES),
    ]
    if glob:
        argv.extend(["--glob", glob])
    argv.extend(["--", pattern, str(root)])
    stdout, stderr, code, output_truncated, timed_out = _run_search(argv)
    if timed_out:
        return [], "Error: search timed out.", False
    matches: list[Match] = []
    for raw in stdout.splitlines():
        if len(matches) > limit:
            break
        try:
            event = json.loads(raw)
        except Exception:
            continue
        if not isinstance(event, dict) or event.get("type") != "match":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        path_data = data.get("path")
        lines_data = data.get("lines")
        if not isinstance(path_data, dict) or not isinstance(lines_data, dict):
            continue
        path_text = path_data.get("text")
        line_text = lines_data.get("text")
        line_number = data.get("line_number")
        if not isinstance(path_text, str) or not isinstance(line_text, str):
            continue
        if not isinstance(line_number, int):
            continue
        path = Path(path_text)
        if keep(path):
            matches.append((str(path), line_number, line_text))
    if code not in (0, 1) and not matches:
        return [], _grep_error(stderr, code), False
    return matches, "", output_truncated


def _grep_error(stderr: str | None, code: int) -> str:
    """Report grep exit 2 as an invalid regex when its wording says so.

    GNU grep says "Invalid regular expression", but BSD grep says things like
    "brackets ([ ]) not balanced" or "trailing backslash". Keep both explicit
    instead of reducing every exit-2 to a generic search failure.
    """
    err = (stderr or "").strip()
    lower = err.lower()
    tokens = (
        "regex",
        "invalid",
        "bracket",
        "unbalanced",
        "backslash",
        "repetition",
        "expression",
        "unmatched",
    )
    if any(token in lower for token in tokens):
        return f"Error: invalid regex: {err[:300]}"
    return f"Error: search failed: {(err or f'exit {code}')[:300]}"


def _parse_grep_line(line: str) -> tuple[Path, int, str] | None:
    match = re.match(r"^(.*?):(\d+):(.*)$", line)
    if not match:
        return None
    try:
        return Path(match.group(1)), int(match.group(2)), match.group(3)
    except Exception:
        return None


def _grep_search(
    root: Path,
    pattern: str,
    glob: str | None,
    limit: int,
    keep: Callable[[Path], bool],
    executable: str,
) -> tuple[list[Match], str, bool]:
    files = list(_candidate_files(root, glob, MAX_SEARCH_FILE_BYTES))
    if not files:
        # grep only compiles the pattern when it has something to scan. Validate
        # against /dev/null so an invalid regex is still an explicit error.
        argv = [executable, "-E", "-n", "-H", "-I", "--", pattern, os.devnull]
        _, stderr, code, _, timed_out = _run_search(argv)
        if timed_out:
            return [], "Error: search timed out.", False
        if code not in (0, 1):
            return [], _grep_error(stderr, code), False
        return [], "", False
    matches: list[Match] = []
    output_truncated = False
    for start in range(0, len(files), GREP_BATCH):
        batch = files[start : start + GREP_BATCH]
        if not batch:
            continue
        argv = [executable, "-E", "-n", "-H", "-I", "--", pattern, *[str(p) for p in batch]]
        stdout, stderr, code, truncated, timed_out = _run_search(argv)
        if timed_out:
            return [], "Error: search timed out.", False
        if code not in (0, 1):
            return [], _grep_error(stderr, code), False
        output_truncated = output_truncated or truncated
        for raw in stdout.splitlines():
            parsed = _parse_grep_line(raw)
            if parsed is None:
                continue
            path, line_number, line_text = parsed
            if not keep(path):
                continue
            matches.append((str(path), line_number, line_text))
            if len(matches) > limit:
                return matches, "", output_truncated
    return matches, "", output_truncated


def _py_search(
    root: Path,
    pattern: str,
    glob: str | None,
    limit: int,
    keep: Callable[[Path], bool],
) -> tuple[list[Match], str, bool]:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return [], f"Error: invalid regex: {exc}", False
    matches: list[Match] = []
    for path in _candidate_files(root, glob, MAX_SEARCH_FILE_BYTES):
        if not keep(path):
            continue
        try:
            with path.open("rb") as file:
                if b"\x00" in file.read(8192):
                    continue
            text = path.read_text(errors="replace")
        except Exception:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                matches.append((str(path), line_number, line))
                if len(matches) > limit:
                    return matches, "", False
    return matches, "", False


def _parse_limit(value: object, default: int, maximum: int) -> tuple[int | None, str | None]:
    limit, err = _parse_optional_int(value, "max_results")
    if err:
        return None, err
    if limit is None:
        limit = default
    if limit < 1:
        return None, "Error: max_results must be >= 1."
    return min(limit, maximum), None


def _resolve_root(path: str) -> Path | str:
    checked = _check_read_path(str(path or "."))
    if isinstance(checked, str):
        return checked
    if not checked.exists():
        return f"Error: {checked} does not exist."
    if not (checked.is_file() or checked.is_dir()):
        return f"Error: {checked} is not a regular file or directory."
    return checked


def tool_search_code(
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    max_results: int = MAX_SEARCH_RESULTS,
) -> str:
    """Search text files and return ``file:line: snippet`` results.

    Uses ripgrep when installed, then grep, then a Python walker. Files under
    .gitignore, hidden paths, symlinks, binary files, and protected roots are
    excluded. Results are capped and truncation is reported explicitly.
    """
    try:
        if not isinstance(pattern, str) or not pattern.strip():
            return "Error: pattern must not be empty."
        if "\n" in pattern or "\r" in pattern:
            return "Error: pattern must be single-line."
        limit, err = _parse_limit(max_results, MAX_SEARCH_RESULTS, MAX_SEARCH_RESULTS_HARD)
        if err:
            return err
        assert limit is not None
        root = _resolve_root(path)
        if isinstance(root, str):
            return root
        if glob is not None and not isinstance(glob, str):
            return "Error: glob must be a string."
        glob = glob.strip() if isinstance(glob, str) and glob.strip() else None
        if glob and ("\n" in glob or "\r" in glob):
            return "Error: glob must be single-line."
        keep = _keep_path_factory(root)
        if root.is_file() and not _glob_filter(root.name, glob):
            return "(no matches)"

        which_rg = shutil.which("rg")
        if which_rg:
            matches, err, output_truncated = _rg_search(root, pattern, glob, limit, keep, which_rg)
        else:
            which_grep = shutil.which("grep")
            if which_grep:
                matches, err, output_truncated = _grep_search(
                    root, pattern, glob, limit, keep, which_grep
                )
            else:
                matches, err, output_truncated = _py_search(root, pattern, glob, limit, keep)
        if err:
            return err
        if not matches:
            if output_truncated:
                return "(no matches)\n... [search output truncated; narrow path or glob]"
            return "(no matches)"
        matches.sort(key=lambda item: (item[0], item[1], item[2]))
        shown = matches[:limit]
        lines = [f"{file}:{line}: {_snippet(text)}" for file, line, text in shown]
        if len(matches) > limit:
            lines.append(f"... [truncated: showing first {limit} matches; increase max_results]")
        elif output_truncated:
            lines.append("... [search output truncated; narrow path or glob]")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


def tool_glob_files(pattern: str, path: str = ".") -> str:
    """Return sorted, capped file paths matching ``pattern``.

    Respects .gitignore, hidden/symlink policy, and the same protected-path
    guard as read_file. A cap of MAX_GLOB_RESULTS is reported explicitly.
    """
    try:
        if not isinstance(pattern, str) or not pattern.strip():
            return "Error: pattern must not be empty."
        if "\n" in pattern or "\r" in pattern:
            return "Error: pattern must be single-line."
        root = _resolve_root(path)
        if isinstance(root, str):
            return root
        matches = [
            str(p)
            for p in _candidate_files(root, pattern.strip(), None)
            if not isinstance(_check_read_path(str(p)), str)
        ]
        matches.sort()
        if not matches:
            return "(no matches)"
        if len(matches) > MAX_GLOB_RESULTS:
            shown = matches[:MAX_GLOB_RESULTS]
            return (
                "\n".join(shown)
                + f"\n... [truncated: showing {MAX_GLOB_RESULTS} of {len(matches)} paths]"
            )
        return "\n".join(matches)
    except Exception as exc:
        return f"Error: {exc}"
