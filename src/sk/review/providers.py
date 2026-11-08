"""Pull-request providers: fetch a PR diff and post review comments.

GitHub uses ``gh`` when it is installed and authenticated (``GH_TOKEN`` also
works). Bitbucket uses the REST API because there is no equivalent CLI.

Posting is comments-only. Nothing in this module edits a branch, pushes a
commit, or applies a suggestion. Missing credentials are a skip, not an error.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from .schema import ReviewReport, _safe_evidence


class ProviderError(RuntimeError):
    """PR metadata/diff/posting failed. The caller reports it and continues."""


@dataclass(frozen=True)
class RemoteRepo:
    provider: str  # github | bitbucket-cloud | bitbucket-server
    host: str
    owner: str
    repo: str
    base_url: str = ""
    remote_url: str = ""


_SSH = re.compile(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$")
_HTTPS = re.compile(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$")


def parse_remote(url: str) -> RemoteRepo | None:
    """Parse a git remote into a provider repo. Returns None when unknown."""
    raw = (url or "").strip()
    if not raw:
        return None
    m = _SSH.match(raw) or _HTTPS.match(raw)
    if not m:
        return None
    host, path = m.group(1).lower(), m.group(2).strip("/")
    parts = [p for p in path.split("/") if p]
    if host == "github.com" and len(parts) >= 2:
        return RemoteRepo("github", host, parts[0], parts[1], "https://api.github.com", raw)
    if host in ("bitbucket.org", "www.bitbucket.org") and len(parts) >= 2:
        return RemoteRepo(
            "bitbucket-cloud",
            "bitbucket.org",
            parts[0],
            parts[1],
            "https://api.bitbucket.org/2.0",
            raw,
        )
    # Bitbucket Server/Data Center: /scm/PROJECT/repo.git or /PROJECT/repo.git
    if "scm" in parts:
        i = parts.index("scm")
        if len(parts) >= i + 3:
            return RemoteRepo(
                "bitbucket-server",
                host,
                parts[i + 1],
                parts[i + 2],
                f"https://{host}",
                raw,
            )
    if ("bitbucket" in host or "stash" in host) and len(parts) >= 2:
        return RemoteRepo(
            "bitbucket-server",
            host,
            parts[-2],
            parts[-1],
            f"https://{host}",
            raw,
        )
    # GitHub Enterprise remotes are usually host/owner/repo.
    if len(parts) >= 2 and not host.startswith("bitbucket"):
        return RemoteRepo(
            "github",
            host,
            parts[-2],
            parts[-1],
            f"https://{host}/api/v3",
            raw,
        )
    return None


def detect_repo(repo_root: str) -> RemoteRepo | None:
    """Read origin (falling back to the first remote) and parse it."""
    urls: list[str] = []
    for remote in ("origin", ""):
        args = (
            ["git", "-C", repo_root, "remote", "get-url", remote]
            if remote
            else [
                "git",
                "-C",
                repo_root,
                "remote",
            ]
        )
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=10)
        except Exception:
            continue
        if remote:
            if r.returncode == 0 and r.stdout.strip():
                urls.append(r.stdout.strip())
            continue
        if r.returncode == 0:
            for name in r.stdout.split():
                rr = subprocess.run(
                    ["git", "-C", repo_root, "remote", "get-url", name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if rr.returncode == 0 and rr.stdout.strip():
                    urls.append(rr.stdout.strip())
    for url in urls:
        repo = parse_remote(url)
        if repo:
            return repo
    return None


def _env(*names: str) -> str:
    for name in names:
        val = os.getenv(name, "").strip()
        if val:
            return val
    return ""


def _keyring(*accounts: str) -> str:
    try:
        from .. import keyring

        for account in accounts:
            val = keyring.get_key(account).strip()
            if val:
                return val
    except Exception:
        pass
    return ""


def bitbucket_secret() -> tuple[dict[str, str], str]:
    """Return (headers, kind) for Bitbucket. Empty headers when unavailable."""
    token = _env("BITBUCKET_TOKEN", "BITBUCKET_ACCESS_TOKEN") or _keyring(
        "bitbucket-token", "bitbucket"
    )
    if token:
        return {"Authorization": f"Bearer {token}"}, "token"
    user = _env("BITBUCKET_USERNAME") or _keyring("bitbucket-username")
    password = _env("BITBUCKET_APP_PASSWORD", "BITBUCKET_PASSWORD") or _keyring(
        "bitbucket-app-password", "bitbucket-password"
    )
    if not password:
        # Combined "user:app-password" secret is convenient in a keyring.
        combined = _keyring("bitbucket-credentials")
        if ":" in combined:
            user, password = combined.split(":", 1)
    if user and password:
        import base64

        raw = f"{user}:{password}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}, "basic"
    return {}, ""


def credentials_available(repo: RemoteRepo) -> tuple[bool, str]:
    """Side-effect-free credential check. Returns (available, kind/note)."""
    if repo.provider == "github":
        gh = shutil.which("gh")
        if gh:
            try:
                r = subprocess.run(
                    [gh, "auth", "status", "--hostname", repo.host],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if r.returncode == 0:
                    return True, "gh"
            except Exception:
                pass
        if _env("GH_TOKEN", "GITHUB_TOKEN"):
            return True, "token"
        return False, "gh not installed/authenticated and no GH_TOKEN"
    headers, kind = bitbucket_secret()
    return (bool(headers), kind or "no Bitbucket credentials")


def _repo_arg(repo: RemoteRepo) -> str:
    if repo.host == "github.com":
        return f"{repo.owner}/{repo.repo}"
    return f"{repo.host}/{repo.owner}/{repo.repo}"


def _gh(repo: RemoteRepo, args: list[str], *, timeout: int = 60) -> str:
    gh = shutil.which("gh")
    if not gh:
        raise ProviderError("gh is not installed")
    try:
        r = subprocess.run(
            [gh, *args, "--repo", _repo_arg(repo)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as e:
        raise ProviderError(f"gh failed: {e}") from e
    if r.returncode != 0:
        raise ProviderError((r.stderr or r.stdout or "gh failed").strip()[:500])
    return r.stdout or ""


def _github_headers() -> dict[str, str]:
    token = _env("GH_TOKEN", "GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_pr_diff(repo_root: str, number: int) -> tuple[str, dict[str, str]]:
    """Fetch the PR diff and the metadata needed for intent/assessment."""
    remote = detect_repo(repo_root)
    if remote is None:
        raise ProviderError("no supported git remote (expected GitHub or Bitbucket)")
    if number <= 0:
        raise ProviderError("PR number must be positive")
    if remote.provider == "github":
        return _fetch_github(remote, number)
    if remote.provider == "bitbucket-cloud":
        return _fetch_bitbucket_cloud(remote, number)
    return _fetch_bitbucket_server(remote, number)


def _fetch_github(repo: RemoteRepo, number: int) -> tuple[str, dict[str, str]]:
    gh = shutil.which("gh")
    if gh:
        try:
            meta_raw = _gh(
                repo,
                [
                    "pr",
                    "view",
                    str(number),
                    "--json",
                    "number,title,body,url,headRefOid,baseRefName,headRepositoryOwner,headRepository",
                ],
            )
            meta = json.loads(meta_raw or "{}")
            diff_text = _gh(repo, ["pr", "diff", str(number)])
            return diff_text, _github_meta(repo, number, meta)
        except Exception:
            # Fall through to token API when gh exists but is not usable.
            if not _env("GH_TOKEN", "GITHUB_TOKEN"):
                raise
    headers = _github_headers()
    base = repo.base_url.rstrip("/")
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            meta_r = client.get(
                f"{base}/repos/{quote(repo.owner)}/{quote(repo.repo)}/pulls/{number}",
                headers=headers,
            )
            meta_r.raise_for_status()
            meta = meta_r.json()
            diff_r = client.get(
                f"{base}/repos/{quote(repo.owner)}/{quote(repo.repo)}/pulls/{number}",
                headers={**headers, "Accept": "application/vnd.github.v3.diff"},
            )
            diff_r.raise_for_status()
            return diff_r.text, _github_meta(repo, number, meta)
    except Exception as e:
        raise ProviderError(f"GitHub API failed: {e}") from e


def _github_meta(repo: RemoteRepo, number: int, meta: dict[str, Any]) -> dict[str, str]:
    owner = meta.get("headRepositoryOwner") or {}
    if isinstance(owner, dict):
        owner = owner.get("login") or repo.owner
    head = meta.get("headRepository") or {}
    if isinstance(head, dict):
        head = head.get("name") or repo.repo
    head_val = meta.get("head")
    base_val = meta.get("base")
    head_obj: dict[str, Any] = head_val if isinstance(head_val, dict) else {}
    base_obj: dict[str, Any] = base_val if isinstance(base_val, dict) else {}
    return {
        "provider": "github",
        "host": repo.host,
        "owner": str(owner),
        "repo": str(head),
        "number": str(number),
        "title": str(meta.get("title") or ""),
        "body": str(meta.get("body") or ""),
        "url": str(meta.get("url") or meta.get("html_url") or ""),
        "head_sha": str(meta.get("headRefOid") or head_obj.get("sha") or ""),
        "base_ref": str(meta.get("baseRefName") or base_obj.get("ref") or ""),
    }


def _fetch_bitbucket_cloud(repo: RemoteRepo, number: int) -> tuple[str, dict[str, str]]:
    headers, kind = bitbucket_secret()
    if not headers:
        raise ProviderError("Bitbucket credentials are not configured")
    base = (
        f"https://api.bitbucket.org/2.0/repositories/{quote(repo.owner)}/{quote(repo.repo)}"
        f"/pullrequests/{number}"
    )
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            meta_r = client.get(base, headers=headers)
            meta_r.raise_for_status()
            meta = meta_r.json()
            diff_r = client.get(base + "/diff", headers=headers)
            diff_r.raise_for_status()
            return diff_r.text, _bitbucket_meta(repo, number, meta, "bitbucket-cloud")
    except Exception as e:
        raise ProviderError(f"Bitbucket API failed: {e}") from e


def _bitbucket_meta(
    repo: RemoteRepo, number: int, meta: dict[str, Any], provider: str
) -> dict[str, str]:
    links = meta.get("links") or {}
    html = (links.get("html") or {}).get("href") if isinstance(links, dict) else ""
    return {
        "provider": provider,
        "host": repo.host,
        "owner": repo.owner,
        "repo": repo.repo,
        "number": str(number),
        "title": str(meta.get("title") or ""),
        "body": str(meta.get("description") or meta.get("body") or ""),
        "url": str(html or ""),
        "head_sha": str((meta.get("fromRef") or {}).get("latestCommit") or ""),
        "base_ref": str((meta.get("toRef") or {}).get("displayId") or ""),
    }


def _fetch_bitbucket_server(repo: RemoteRepo, number: int) -> tuple[str, dict[str, str]]:
    headers, _ = bitbucket_secret()
    if not headers:
        raise ProviderError("Bitbucket credentials are not configured")
    base = (
        f"{repo.base_url}/rest/api/1.0/projects/{quote(repo.owner)}/repos/"
        f"{quote(repo.repo)}/pull-requests/{number}"
    )
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            meta_r = client.get(base, headers=headers)
            meta_r.raise_for_status()
            meta = meta_r.json()
            # Server returns structured hunks; convert them to unified text so
            # the same parser and prompts work for every provider.
            diff_r = client.get(base + "/diff", headers=headers, params={"withComments": "false"})
            diff_r.raise_for_status()
            diff_text = _server_diff_to_unified(diff_r.json())
            if not diff_text.strip():
                raise ProviderError("Bitbucket Server returned an empty diff")
            return diff_text, _bitbucket_meta(repo, number, meta, "bitbucket-server")
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"Bitbucket Server API failed: {e}") from e


def _server_diff_to_unified(data: dict[str, Any]) -> str:
    out: list[str] = []
    for d in data.get("diffs") or []:
        src = ((d.get("source") or {}).get("toString") or "").strip()
        dst = ((d.get("destination") or {}).get("toString") or "").strip()
        if not src and not dst:
            continue
        out.append(f"diff --git a/{src} b/{dst}")
        if src and not dst:
            out.append("--- a/" + src)
            out.append("+++ /dev/null")
        elif dst and not src:
            out.append("--- /dev/null")
            out.append("+++ b/" + dst)
        else:
            out.append("--- a/" + src)
            out.append("+++ b/" + dst)
        for h in d.get("hunks") or []:
            sl = int(h.get("sourceLine") or 0)
            ss = int(h.get("sourceSpan") or 0)
            dl = int(h.get("destinationLine") or 0)
            ds = int(h.get("destinationSpan") or 0)
            out.append(f"@@ -{sl},{ss} +{dl},{ds} @@")
            for seg in h.get("segments") or []:
                typ = str(seg.get("type") or "CONTEXT").upper()
                for line in seg.get("lines") or []:
                    text = str(line.get("text") or "")
                    if typ == "ADDED":
                        out.append("+" + text)
                    elif typ == "REMOVED":
                        out.append("-" + text)
                    else:
                        out.append(" " + text)
    return "\n".join(out) + ("\n" if out else "")


def post_review(
    repo_root: str,
    number: int,
    report: ReviewReport,
    *,
    diff: Any = None,
    session: str = "",
) -> tuple[str, str]:
    """Post findings as PR comments. Returns (url, note). Never raises."""
    remote = detect_repo(repo_root)
    if remote is None:
        return "", "no supported git remote; review was not posted"
    available, kind = credentials_available(remote)
    if not available:
        return "", f"{remote.provider} credentials unavailable ({kind}); review was not posted"
    body = _post_body(report)
    try:
        if remote.provider == "github":
            url = _post_github(remote, number, report, body, diff=diff, kind=kind)
        elif remote.provider == "bitbucket-cloud":
            url = _post_bitbucket_cloud(remote, number, report, body, diff=diff)
        else:
            url = _post_bitbucket_server(remote, number, body)
        _audit(session, remote, url, number)
        return url, ""
    except Exception as e:  # noqa: BLE001 - posting must never break a review
        return "", f"posting failed: {str(e)[:300]}"


def _post_body(report: ReviewReport) -> str:
    text = report.to_markdown()
    # Strip a previous posting footer if the same report is retried.
    text = re.sub(r"\nPosted review: .*$", "", text)
    return text.strip()[:60_000]


def _inline_body(f: Any, diff: Any) -> str | None:
    if diff is None:
        return None
    fd = diff.by_path().get(f.file)
    if fd is None or f.line not in fd.new_lines_in_hunk():
        return None
    parts = [f"**{f.severity.upper()} · {f.category}**", "", f.problem]
    if f.evidence:
        parts.extend(["", f"> `{_safe_evidence(f.evidence)}`"])
    if f.suggestion:
        parts.extend(["", f"**Fix:** {f.suggestion}"])
    return "\n".join(parts)[:4000]


def _post_github(
    repo: RemoteRepo,
    number: int,
    report: ReviewReport,
    body: str,
    *,
    diff: Any,
    kind: str,
) -> str:
    comments = []
    for f in report.findings[:20]:
        inline = _inline_body(f, diff)
        if inline:
            comments.append({"path": f.file, "line": f.line, "side": "RIGHT", "body": inline})
    payload = {"body": body, "event": "COMMENT", "comments": comments}
    endpoint = f"repos/{repo.owner}/{repo.repo}/pulls/{number}/reviews"
    if kind == "gh":
        gh = shutil.which("gh")
        if gh:
            r = subprocess.run(
                [gh, "api", "--method", "POST", endpoint, "--input", "-"],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode == 0:
                try:
                    data = json.loads(r.stdout or "{}")
                    return str(data.get("html_url") or data.get("url") or _pr_url(repo, number))
                except Exception:
                    return _pr_url(repo, number)
            # A inline-comment validation error should not lose the summary.
            fallback = subprocess.run(
                [
                    gh,
                    "pr",
                    "comment",
                    str(number),
                    "--repo",
                    f"{repo.owner}/{repo.repo}",
                    "--body",
                    body,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if fallback.returncode == 0:
                return _pr_url(repo, number)
            raise ProviderError((r.stderr or fallback.stderr or "gh api failed").strip()[:500])
    base = repo.base_url.rstrip("/")
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            resp = client.post(
                f"{base}/repos/{quote(repo.owner)}/{quote(repo.repo)}/pulls/{number}/reviews",
                headers=_github_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return str(data.get("html_url") or _pr_url(repo, number))
    except Exception as e:
        raise ProviderError(f"GitHub posting failed: {e}") from e


def _post_bitbucket_cloud(
    repo: RemoteRepo, number: int, report: ReviewReport, body: str, *, diff: Any
) -> str:
    headers, _ = bitbucket_secret()
    url = (
        f"https://api.bitbucket.org/2.0/repositories/{quote(repo.owner)}/{quote(repo.repo)}"
        f"/pullrequests/{number}/comments"
    )
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            first = client.post(url, headers=headers, json={"content": {"raw": body}})
            first.raise_for_status()
            data = first.json()
            html = ((data.get("links") or {}).get("html") or {}).get("href") or _pr_url(
                repo, number
            )
            # Inline comments are best-effort; a failure here does not undo the
            # summary comment.
            for f in report.findings[:10]:
                inline = _inline_body(f, diff)
                if not inline:
                    continue
                try:
                    client.post(
                        url,
                        headers=headers,
                        json={
                            "content": {"raw": inline},
                            "inline": {"path": f.file, "to": f.line},
                        },
                    )
                except Exception:
                    continue
            return str(html)
    except Exception as e:
        raise ProviderError(f"Bitbucket posting failed: {e}") from e


def _post_bitbucket_server(repo: RemoteRepo, number: int, body: str) -> str:
    headers, _ = bitbucket_secret()
    url = (
        f"{repo.base_url}/rest/api/1.0/projects/{quote(repo.owner)}/repos/"
        f"{quote(repo.repo)}/pull-requests/{number}/comments"
    )
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            r = client.post(url, headers=headers, json={"text": body})
            r.raise_for_status()
            return _pr_url(repo, number)
    except Exception as e:
        raise ProviderError(f"Bitbucket Server posting failed: {e}") from e


def _pr_url(repo: RemoteRepo, number: int) -> str:
    if repo.provider == "bitbucket-server":
        return f"{repo.base_url}/projects/{repo.owner}/repos/{repo.repo}/pull-requests/{number}"
    if repo.provider == "bitbucket-cloud":
        return f"https://bitbucket.org/{repo.owner}/{repo.repo}/pull-requests/{number}"
    return f"https://{repo.host}/{repo.owner}/{repo.repo}/pull/{number}"


def _audit(session: str, repo: RemoteRepo, url: str, number: int) -> None:
    try:
        from ..store import log_tool_run

        log_tool_run(
            session or "review",
            "review.post",
            target=f"{repo.provider}:{repo.owner}/{repo.repo}#{number} {url}"[:500],
            approved=True,
            provider=repo.provider,
            host=repo.host,
            ok=True,
        )
    except Exception:
        pass
