"""Resolve review targets and collect a small, structured diff model.

Read-only and shell-approval-free: this module only runs ``git`` plumbing
commands with argv (never a shell). The model sees the diff as untrusted data.

Targets:
- working tree + staged (``git diff HEAD``)
- staged only (``git diff --cached``)
- branch vs base (merge-base diff)
- one commit (parent..commit)
- path filter
- pull request (delegated to ``providers``; the diff text is parsed here)
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Iterable

DEFAULT_MAX_FILES = 200
DEFAULT_MAX_DIFF_BYTES = 400_000

_GENERATED = re.compile(
    r"(^|/)("
    r"node_modules|vendor|dist|build|\.next|\.nuxt|__pycache__|\.venv|venv|"
    r"\.mypy_cache|\.pytest_cache|coverage|htmlcov|target"
    r")/",
    re.IGNORECASE,
)
_LOCKFILES = re.compile(
    r"(^|/)("
    r"package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock|"
    r"Pipfile\.lock|Cargo\.lock|go\.sum|composer\.lock|Gemfile\.lock"
    r")$",
    re.IGNORECASE,
)
_GENERATED_SUFFIX = re.compile(r"\.(min\.js|min\.css|map|lock|snap|generated\.\w+)$", re.IGNORECASE)

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


class DiffError(RuntimeError):
    """A target could not be resolved. The message is user-facing."""


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: tuple[str, ...] = ()

    def new_range(self) -> range:
        """New-file line numbers covered by this hunk (incl. context)."""
        count = max(self.new_count, 1)
        return range(self.new_start, self.new_start + count)


@dataclass(frozen=True)
class FileDiff:
    path: str
    old_path: str = ""
    status: str = "M"
    binary: bool = False
    additions: int = 0
    deletions: int = 0
    hunks: tuple[Hunk, ...] = ()
    skipped: str = ""

    def changed_new_lines(self) -> set[int]:
        """Added lines (``+``) in the new file, excluding the ``+++`` header."""
        out: set[int] = set()
        for h in self.hunks:
            line_no = h.new_start
            for raw in h.lines:
                if raw.startswith("\\"):
                    continue
                if raw.startswith("+"):
                    out.add(line_no)
                if not raw.startswith("-"):
                    line_no += 1
        return out

    def new_lines_in_hunk(self) -> set[int]:
        out: set[int] = set()
        for h in self.hunks:
            out.update(h.new_range())
        return out


@dataclass(frozen=True)
class SkipReason:
    path: str
    reason: str


@dataclass(frozen=True)
class DiffModel:
    target: str
    repo_root: str
    ref: str
    files: tuple[FileDiff, ...] = ()
    skipped: tuple[SkipReason, ...] = ()
    raw: str = ""
    truncated: bool = False
    changed_lines: int = 0
    meta: dict[str, str] = field(default_factory=dict)

    def by_path(self) -> dict[str, FileDiff]:
        return {f.path: f for f in self.files}

    def text(self) -> str:
        return self.raw


def _run_git(repo: str, *args: str, timeout: int = 30) -> tuple[bool, str, str]:
    try:
        r = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode == 0, (r.stdout or ""), (r.stderr or "").strip()
    except Exception as e:
        return False, "", str(e)


def repo_root(cwd: str = "") -> str:
    ok, out, err = _run_git(cwd or ".", "rev-parse", "--show-toplevel")
    if not ok or not out.strip():
        raise DiffError(f"not a git repository: {cwd or '.'} ({err or 'git rev-parse failed'})")
    return out.strip()


def resolve_target(
    *,
    cwd: str = "",
    staged: bool = False,
    base: str = "",
    commit: str = "",
    pr: int = 0,
    path: str = "",
) -> str:
    """Return a compact target descriptor consumed by :func:`collect_diff`."""
    if pr:
        return f"pr:{int(pr)}"
    if commit.strip():
        return f"commit:{commit.strip()}"
    if base.strip():
        return f"base:{base.strip()}"
    if staged:
        return "staged"
    return "worktree"


def _diff_args(repo: str, target: str, path: str) -> tuple[list[str], str]:
    """Return (git argv after `diff`, human ref label)."""
    args = ["--no-color", "--no-ext-diff", "--find-renames", "--unified=3"]
    if target == "staged":
        args.append("--cached")
        ref = "staged"
    elif target.startswith("base:"):
        base = target.split(":", 1)[1]
        ok, mb, err = _run_git(repo, "merge-base", base, "HEAD")
        if not ok or not mb.strip():
            raise DiffError(f"could not resolve merge-base for {base!r}: {err or 'unknown ref'}")
        args.append(mb.strip())
        args.append("HEAD")
        ref = f"{base}...HEAD"
    elif target.startswith("commit:"):
        sha = target.split(":", 1)[1]
        ok, parent, _ = _run_git(repo, "rev-parse", f"{sha}^")
        if ok and parent.strip():
            args.extend([parent.strip(), sha])
            ref = sha
        else:
            # Root commit: git show emits the commit as a full addition.
            args = ["show", "--no-color", "--no-ext-diff", "--unified=3", "--format=", sha]
            ref = sha
    else:
        args.append("HEAD")
        ref = "HEAD"
    if path.strip():
        args.extend(["--", path.strip()])
    return args, ref


def _parse_name_status(text: str) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status = parts[0][:1] or "M"
            if status == "R" and len(parts) >= 3:
                out[parts[2]] = ("R", parts[1])
            else:
                out[parts[1]] = (status, "")
    return out


def _parse_numstat(text: str) -> dict[str, tuple[int, int, bool]]:
    out: dict[str, tuple[int, int, bool]] = {}
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        adds, dels, path = parts[0], parts[1], parts[2]
        binary = adds == "-" or dels == "-"
        try:
            out[path] = (int(adds or 0), int(dels or 0), binary)
        except ValueError:
            out[path] = (0, 0, True)
    return out


def parse_unified_diff(
    text: str,
    *,
    name_status: dict[str, tuple[str, str]] | None = None,
    numstat: dict[str, tuple[int, int, bool]] | None = None,
    max_files: int = DEFAULT_MAX_FILES,
) -> tuple[tuple[FileDiff, ...], tuple[SkipReason, ...]]:
    """Parse a git unified diff into FileDiff/Hunk records.

    Unknown/malformed sections are ignored rather than raising: a review must
    still work on unusual diffs. Binary files are marked, not read.
    """
    files: list[FileDiff] = []
    skipped: list[SkipReason] = []
    name_status = name_status or {}
    numstat = numstat or {}

    cur_path = ""
    cur_old = ""
    cur_status = "M"
    cur_binary = False
    cur_hunks: list[Hunk] = []
    hunk_header = ""
    hunk_old_start = hunk_old_count = hunk_new_start = hunk_new_count = 0
    hunk_lines: list[str] = []
    in_hunk = False

    def flush_hunk() -> None:
        nonlocal hunk_lines, in_hunk, hunk_header
        if not in_hunk:
            return
        cur_hunks.append(
            Hunk(
                old_start=hunk_old_start,
                old_count=hunk_old_count,
                new_start=hunk_new_start,
                new_count=hunk_new_count,
                header=hunk_header,
                lines=tuple(hunk_lines),
            )
        )
        hunk_lines = []
        in_hunk = False

    def flush_file() -> None:
        nonlocal cur_path, cur_old, cur_status, cur_binary, cur_hunks
        flush_hunk()
        if not cur_path:
            cur_hunks = []
            return
        adds, dels, binary = numstat.get(cur_path, (0, 0, cur_binary))
        if cur_path not in numstat:
            adds = sum(1 for h in cur_hunks for ln in h.lines if ln.startswith("+"))
            dels = sum(1 for h in cur_hunks for ln in h.lines if ln.startswith("-"))
        status, old = name_status.get(cur_path, (cur_status, cur_old))
        if not cur_binary:
            cur_binary = binary
        reason = _skip_reason(cur_path, cur_binary)
        if reason:
            skipped.append(SkipReason(cur_path, reason))
        elif len(files) >= max_files:
            skipped.append(SkipReason(cur_path, f"file cap reached ({max_files})"))
        else:
            files.append(
                FileDiff(
                    path=cur_path,
                    old_path=old,
                    status=status,
                    binary=cur_binary,
                    additions=adds,
                    deletions=dels,
                    hunks=tuple(cur_hunks),
                )
            )
        cur_path = ""
        cur_old = ""
        cur_status = "M"
        cur_binary = False
        cur_hunks = []

    for line in (text or "").splitlines():
        if line.startswith("diff --git "):
            flush_file()
            m = re.match(r"diff --git a/(.*?) b/(.*)$", line)
            if m:
                cur_old = m.group(1)
                cur_path = m.group(2)
                cur_status = "M"
            continue
        if line.startswith("new file mode"):
            cur_status = "A"
            continue
        if line.startswith("deleted file mode"):
            cur_status = "D"
            continue
        if line.startswith("rename from "):
            cur_old = line[len("rename from ") :].strip()
            continue
        if line.startswith("rename to "):
            cur_path = line[len("rename to ") :].strip()
            cur_status = "R"
            continue
        if line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            cur_binary = True
            continue
        if line.startswith("--- "):
            if line.startswith("--- /dev/null"):
                cur_status = "A"
            continue
        if line.startswith("+++ "):
            if line.startswith("+++ /dev/null"):
                cur_status = "D"
            else:
                p = line[4:].strip()
                if p.startswith("b/"):
                    p = p[2:]
                if p and p != cur_path:
                    cur_old = cur_old or cur_path
                    cur_path = p
            continue
        m = _HUNK.match(line)
        if m:
            flush_hunk()
            hunk_old_start = int(m.group(1))
            hunk_old_count = int(m.group(2) or "1")
            hunk_new_start = int(m.group(3))
            hunk_new_count = int(m.group(4) or "1")
            hunk_header = line
            hunk_lines = []
            in_hunk = True
            continue
        if in_hunk:
            if line.startswith(("+", "-", " ", "\\")):
                hunk_lines.append(line)
            else:
                # A malformed trailer ends the hunk; keep what we have.
                flush_hunk()
    flush_file()

    # Files present in name-status but absent from the textual diff are still
    # reported as skipped, so coverage never silently lies.
    seen = {f.path for f in files} | {s.path for s in skipped}
    for path in name_status:
        if path not in seen:
            skipped.append(SkipReason(path, "no textual diff (binary or mode-only)"))
    return tuple(files), tuple(skipped)


def _skip_reason(path: str, binary: bool) -> str:
    if binary:
        return "binary file"
    if _GENERATED.search(path):
        return "generated/vendor path"
    if _LOCKFILES.search(path) or _GENERATED_SUFFIX.search(path):
        return "lockfile/generated suffix"
    return ""


def collect_diff(
    *,
    cwd: str = "",
    staged: bool = False,
    base: str = "",
    commit: str = "",
    pr: int = 0,
    path: str = "",
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_DIFF_BYTES,
    diff_text: str = "",
    pr_meta: dict[str, str] | None = None,
) -> DiffModel:
    """Collect a reviewable diff. Never writes; raises DiffError on bad targets."""
    repo = repo_root(cwd)
    target = resolve_target(cwd=repo, staged=staged, base=base, commit=commit, pr=pr, path=path)
    meta = dict(pr_meta or {})
    if pr:
        if not diff_text:
            from .providers import fetch_pr_diff

            diff_text, provider_meta = fetch_pr_diff(repo, int(pr))
            meta.update(provider_meta)
        ref = f"PR #{int(pr)}"
    else:
        args, ref = _diff_args(repo, target, path)
        # `commit:` may return a `git show` argv; everything else is `git diff`.
        if args and args[0] == "show":
            ok, diff_text, err = _run_git(repo, *args)
        else:
            ok, diff_text, err = _run_git(repo, "diff", *args)
        if not ok:
            raise DiffError(f"could not collect diff: {err or 'git failed'}")
        # Metadata commands must target the same range as the textual diff.
        ns_args: list[str] = []
        root_commit = False
        if target.startswith("base:"):
            mb_ok, mb, _ = _run_git(repo, "merge-base", target.split(":", 1)[1], "HEAD")
            ns_args = [mb.strip(), "HEAD"] if mb_ok and mb.strip() else []
        elif target.startswith("commit:"):
            sha = target.split(":", 1)[1]
            p_ok, parent, _ = _run_git(repo, "rev-parse", f"{sha}^")
            if p_ok and parent.strip():
                ns_args = [parent.strip(), sha]
            else:
                root_commit = True
                ns_args = [sha]
        elif target == "staged":
            ns_args = ["--cached"]
        else:
            ns_args = ["HEAD"]
        if root_commit:
            ok_ns, ns_text, _ = _run_git(repo, "show", "--name-status", "--format=", *ns_args)
            ok_num, num_text, _ = _run_git(repo, "show", "--numstat", "--format=", *ns_args)
        else:
            ok_ns, ns_text, _ = _run_git(repo, "diff", "--name-status", *ns_args)
            ok_num, num_text, _ = _run_git(repo, "diff", "--numstat", *ns_args)
        name_status = _parse_name_status(ns_text) if ok_ns else {}
        numstat = _parse_numstat(num_text) if ok_num else {}

    truncated = len(diff_text) > max_bytes
    body = diff_text[:max_bytes] if truncated else diff_text

    if pr:
        name_status = _parse_name_status(_pr_name_status(diff_text))
        numstat = {}
    files, skipped = parse_unified_diff(
        body, name_status=name_status, numstat=numstat, max_files=max_files
    )
    changed = sum(len(f.changed_new_lines()) for f in files)
    return DiffModel(
        target=target,
        repo_root=repo,
        ref=ref,
        files=files,
        skipped=skipped,
        raw=body,
        truncated=truncated,
        changed_lines=changed,
        meta=meta,
    )


def _pr_name_status(diff_text: str) -> str:
    """Best-effort name/status lines from a PR diff (already unified)."""
    out: list[str] = []
    for m in re.finditer(r"^diff --git a/(.*?) b/(.*)$", diff_text, re.MULTILINE):
        out.append(f"M\t{m.group(2)}")
    return "\n".join(out)


def changed_line_sets(diff: DiffModel) -> dict[str, set[int]]:
    return {f.path: f.changed_new_lines() for f in diff.files}


def iter_chunks(diff: DiffModel, max_chars: int = 12_000) -> Iterable[str]:
    """Yield one or more fenced diff chunks, preserving file boundaries."""
    chunk: list[str] = []
    used = 0
    for f in diff.files:
        body = f"### {f.path} ({f.status})\n" + "\n".join(
            [ln for h in f.hunks for ln in (h.header, *h.lines)]
        )
        if chunk and used + len(body) > max_chars:
            yield "\n\n".join(chunk)
            chunk = []
            used = 0
        if len(body) > max_chars:
            # A single huge file still goes in one piece; the engine caps it.
            body = body[:max_chars] + "\n[diff chunk truncated]"
        chunk.append(body)
        used += len(body)
    if chunk:
        yield "\n\n".join(chunk)
