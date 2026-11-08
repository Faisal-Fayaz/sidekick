"""Resolve the stated intent for the worth-it assessment.

Never invent intent. If none is available, ``available`` is False and the
assessment records an explicit assumption instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass

from .diff import DiffModel, _run_git


@dataclass(frozen=True)
class Intent:
    text: str = ""
    source: str = "inferred"

    @property
    def available(self) -> bool:
        return bool(self.text.strip())


def _commit_message(repo: str, rev: str) -> str:
    ok, out, _ = _run_git(repo, "show", "-s", "--format=%B", rev)
    return out.strip() if ok else ""


def _branch(repo: str) -> str:
    ok, out, _ = _run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if ok else ""


def resolve_intent(
    diff: DiffModel,
    *,
    explicit: str = "",
    pr_meta: dict[str, str] | None = None,
) -> Intent:
    """Priority: --intent, PR body/title, commit message(s), branch name."""
    if explicit.strip():
        return Intent(explicit.strip()[:4000], "user")
    meta = pr_meta or diff.meta or {}
    body = str(meta.get("body") or "").strip()
    title = str(meta.get("title") or "").strip()
    if body or title:
        text = title
        if body:
            text = (text + "\n\n" + body).strip()
        if text:
            return Intent(text[:4000], "pr-body")

    if diff.target.startswith("base:"):
        base = diff.target.split(":", 1)[1]
        ok, out, _ = _run_git(diff.repo_root, "log", "--format=%B%n---%n", f"{base}..HEAD")
        text = out.strip() if ok else ""
        if text:
            return Intent(text[:4000], "commit")
    elif diff.target.startswith("commit:"):
        sha = diff.target.split(":", 1)[1]
        text = _commit_message(diff.repo_root, sha)
        if text:
            return Intent(text[:4000], "commit")

    branch = _branch(diff.repo_root)
    if branch and branch not in ("HEAD", "main", "master", "develop", "trunk"):
        pretty = branch.replace("-", " ").replace("_", " ").strip()
        if pretty:
            return Intent(pretty[:1000], "branch")
    return Intent("", "inferred")
