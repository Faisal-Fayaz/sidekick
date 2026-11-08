"""PR providers: remote parsing, auth detection, payload building, failure safety."""

from __future__ import annotations

import json

import httpx

from sk.review import providers
from sk.review.diff import DiffModel, FileDiff, Hunk
from sk.review.schema import Finding, ReviewReport


def test_parse_remote_variants():
    gh = providers.parse_remote("git@github.com:owner/repo.git")
    assert gh and gh.provider == "github" and gh.owner == "owner" and gh.repo == "repo"

    gh_https = providers.parse_remote("https://github.com/owner/repo.git")
    assert gh_https and gh_https.provider == "github"

    bb = providers.parse_remote("git@bitbucket.org:workspace/repo.git")
    assert bb and bb.provider == "bitbucket-cloud" and bb.owner == "workspace"

    bb_https = providers.parse_remote("https://user@bitbucket.org/workspace/repo.git")
    assert bb_https and bb_https.provider == "bitbucket-cloud"

    server = providers.parse_remote("ssh://git@bitbucket.example.com:7999/PROJ/repo.git")
    assert server and server.provider == "bitbucket-server"
    assert server.owner == "PROJ" and server.repo == "repo"

    server_https = providers.parse_remote("https://bitbucket.example.com/scm/PROJ/repo.git")
    assert server_https and server_https.provider == "bitbucket-server"
    assert server_https.owner == "PROJ"

    assert providers.parse_remote("https://gitlab.com/owner/repo.git") is not None
    assert providers.parse_remote("") is None


def test_detect_repo_reads_origin(tmp_path):
    import shutil
    import subprocess

    if shutil.which("git") is None:
        import pytest

        pytest.skip("needs git")
    d = tmp_path / "r"
    d.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:owner/repo.git"],
        cwd=d,
        check=True,
    )
    repo = providers.detect_repo(str(d))
    assert repo is not None
    assert repo.provider == "github"


def test_bitbucket_secret_env(monkeypatch):
    monkeypatch.setattr(providers, "_keyring", lambda *a: "")
    monkeypatch.setenv("BITBUCKET_TOKEN", "tok")
    headers, kind = providers.bitbucket_secret()
    assert kind == "token"
    assert headers["Authorization"] == "Bearer tok"
    monkeypatch.delenv("BITBUCKET_TOKEN")
    monkeypatch.setenv("BITBUCKET_USERNAME", "u")
    monkeypatch.setenv("BITBUCKET_APP_PASSWORD", "p")
    headers, kind = providers.bitbucket_secret()
    assert kind == "basic"
    assert headers["Authorization"].startswith("Basic ")


def test_credentials_available_github_without_gh(monkeypatch):
    monkeypatch.setattr(providers.shutil, "which", lambda _: None)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    repo = providers.RemoteRepo("github", "github.com", "o", "r")
    ok, note = providers.credentials_available(repo)
    assert ok is False
    assert "gh" in note


def _report() -> ReviewReport:
    f = Finding(
        id="F001",
        severity="major",
        category="security",
        file="app.py",
        line=2,
        end_line=None,
        evidence='y = eval("1")',
        problem="eval on input",
        impact="code execution",
        suggestion="parse",
        confidence="high",
        rule_id="test/eval",
        source="check",
    )
    return ReviewReport(
        target="pr:1",
        ref="PR #1",
        repo_root="/tmp",
        findings=[f],
        changed_lines=1,
        reviewed_lines=1,
        files_reviewed=1,
    )


def _diff() -> DiffModel:
    return DiffModel(
        target="pr:1",
        repo_root="/tmp",
        ref="PR #1",
        files=(
            FileDiff(
                path="app.py",
                hunks=(Hunk(1, 1, 1, 2, "@@ -1 +1,2 @@", (" x = 1", '+y = eval("1")')),),
            ),
        ),
    )


def test_post_review_without_credentials_is_a_note(monkeypatch):
    monkeypatch.setattr(
        providers,
        "detect_repo",
        lambda root: providers.RemoteRepo("github", "github.com", "o", "r"),
    )
    monkeypatch.setattr(providers, "credentials_available", lambda repo: (False, "nope"))
    url, note = providers.post_review("/tmp", 1, _report())
    assert url == ""
    assert "not posted" in note


def test_post_github_builds_review_payload(monkeypatch):
    calls = []

    class Done:
        returncode = 0
        stdout = json.dumps({"html_url": "https://github.com/o/r/pull/1#review"})
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Done()

    monkeypatch.setattr(providers.shutil, "which", lambda _: "/fake/gh")
    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    repo = providers.RemoteRepo("github", "github.com", "o", "r")
    url = providers._post_github(
        repo,
        1,
        _report(),
        "summary",
        diff=_diff(),
        kind="gh",
    )
    assert url.endswith("#review")
    argv, kwargs = calls[0]
    assert argv[:4] == ["/fake/gh", "api", "--method", "POST"]
    payload = json.loads(kwargs["input"])
    assert payload["body"] == "summary"
    assert payload["event"] == "COMMENT"
    assert payload["comments"][0]["path"] == "app.py"
    assert payload["comments"][0]["line"] == 2
    assert "eval" in payload["comments"][0]["body"]


def test_post_bitbucket_cloud_posts_summary_and_inline(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"links": {"html": {"href": "https://bitbucket.org/o/r/pull-requests/1"}}}
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def fake_client(**kwargs):
        kwargs.pop("timeout", None)
        kwargs.pop("follow_redirects", None)
        return real_client(transport=transport)

    monkeypatch.setattr(providers.httpx, "Client", fake_client)
    monkeypatch.setattr(
        providers, "bitbucket_secret", lambda: ({"Authorization": "Bearer x"}, "token")
    )
    repo = providers.RemoteRepo(
        "bitbucket-cloud", "bitbucket.org", "o", "r", "https://api.bitbucket.org/2.0"
    )
    url = providers._post_bitbucket_cloud(repo, 1, _report(), "summary", diff=_diff())
    assert "pull-requests/1" in url
    assert len(seen) == 2
    assert json.loads(seen[0].content)["content"]["raw"] == "summary"
    inline = json.loads(seen[1].content)
    assert inline["inline"] == {"path": "app.py", "to": 2}


def test_server_diff_to_unified():
    data = {
        "diffs": [
            {
                "source": {"toString": "a.py"},
                "destination": {"toString": "a.py"},
                "hunks": [
                    {
                        "sourceLine": 1,
                        "sourceSpan": 1,
                        "destinationLine": 1,
                        "destinationSpan": 2,
                        "segments": [
                            {"type": "CONTEXT", "lines": [{"text": "x = 1"}]},
                            {"type": "ADDED", "lines": [{"text": "y = 2"}]},
                        ],
                    }
                ],
            }
        ]
    }
    text = providers._server_diff_to_unified(data)
    assert "diff --git a/a.py b/a.py" in text
    assert "@@ -1,1 +1,2 @@" in text
    assert "+y = 2" in text


def test_github_meta_tolerates_string_head():
    repo = providers.RemoteRepo("github", "github.com", "o", "r")
    meta = providers._github_meta(
        repo,
        1,
        {"title": "t", "head": "abc", "base": "main"},
    )
    assert meta["title"] == "t"
    assert meta["head_sha"] == ""
    assert meta["base_ref"] == ""


def test_credentials_available_bitbucket(monkeypatch):
    monkeypatch.setattr(providers, "bitbucket_secret", lambda: ({"Authorization": "x"}, "token"))
    repo = providers.RemoteRepo("bitbucket-cloud", "bitbucket.org", "o", "r")
    ok, kind = providers.credentials_available(repo)
    assert ok is True and kind == "token"
    monkeypatch.setattr(providers, "bitbucket_secret", lambda: ({}, ""))
    ok, kind = providers.credentials_available(repo)
    assert ok is False and "no Bitbucket" in kind


def test_fetch_github_token_api(monkeypatch):
    repo = providers.RemoteRepo("github", "github.com", "o", "r", "https://api.github.com")

    def handler(request: httpx.Request) -> httpx.Response:
        if "diff" in request.headers.get("accept", ""):
            return httpx.Response(200, text="diff --git a/app.py b/app.py\n+y\n")
        return httpx.Response(
            200,
            json={
                "title": "T",
                "body": "B",
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
                "base": {"ref": "main"},
            },
        )

    transport = httpx.MockTransport(handler)
    real = httpx.Client

    def fake_client(**kwargs):
        return real(transport=transport)

    monkeypatch.setattr(providers.httpx, "Client", fake_client)
    monkeypatch.setattr(providers.shutil, "which", lambda _: None)
    monkeypatch.setenv("GH_TOKEN", "x")
    diff, meta = providers._fetch_github(repo, 1)
    assert "+y" in diff
    assert meta["title"] == "T"
    assert meta["head_sha"] == "abc"
    assert meta["base_ref"] == "main"


def test_fetch_bitbucket_cloud_mock(monkeypatch):
    repo = providers.RemoteRepo(
        "bitbucket-cloud", "bitbucket.org", "o", "r", "https://api.bitbucket.org/2.0"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/diff"):
            return httpx.Response(200, text="diff --git a/app.py b/app.py\n+y\n")
        return httpx.Response(
            200,
            json={
                "title": "T",
                "description": "B",
                "links": {"html": {"href": "https://bitbucket.org/o/r/pull-requests/1"}},
                "fromRef": {"latestCommit": "abc"},
                "toRef": {"displayId": "main"},
            },
        )

    transport = httpx.MockTransport(handler)
    real = httpx.Client

    def fake_client(**kwargs):
        return real(transport=transport)

    monkeypatch.setattr(providers.httpx, "Client", fake_client)
    monkeypatch.setattr(providers, "bitbucket_secret", lambda: ({"Authorization": "x"}, "token"))
    diff, meta = providers._fetch_bitbucket_cloud(repo, 1)
    assert "+y" in diff
    assert meta["title"] == "T"
    assert meta["head_sha"] == "abc"


def test_fetch_bitbucket_server_mock(monkeypatch):
    repo = providers.RemoteRepo(
        "bitbucket-server", "bb.example.com", "PROJ", "repo", "https://bb.example.com"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/diff"):
            return httpx.Response(
                200,
                json={
                    "diffs": [
                        {
                            "source": {"toString": "app.py"},
                            "destination": {"toString": "app.py"},
                            "hunks": [
                                {
                                    "sourceLine": 1,
                                    "sourceSpan": 1,
                                    "destinationLine": 1,
                                    "destinationSpan": 2,
                                    "segments": [{"type": "ADDED", "lines": [{"text": "y"}]}],
                                }
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "title": "T",
                "description": "B",
                "links": {"self": [{"href": "https://bb.example.com/pr/1"}]},
            },
        )

    transport = httpx.MockTransport(handler)
    real = httpx.Client

    def fake_client(**kwargs):
        return real(transport=transport)

    monkeypatch.setattr(providers.httpx, "Client", fake_client)
    monkeypatch.setattr(providers, "bitbucket_secret", lambda: ({"Authorization": "x"}, "token"))
    diff, meta = providers._fetch_bitbucket_server(repo, 1)
    assert "+y" in diff
    assert meta["title"] == "T"


def test_post_github_token_api(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/1#review"})

    transport = httpx.MockTransport(handler)
    real = httpx.Client

    def fake_client(**kwargs):
        return real(transport=transport)

    monkeypatch.setattr(providers.httpx, "Client", fake_client)
    monkeypatch.setenv("GH_TOKEN", "x")
    repo = providers.RemoteRepo("github", "github.com", "o", "r", "https://api.github.com")
    url = providers._post_github(repo, 1, _report(), "summary", diff=_diff(), kind="token")
    assert url.endswith("#review")
    payload = json.loads(seen[0].content)
    assert payload["comments"][0]["path"] == "app.py"


def test_post_bitbucket_server(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={})

    transport = httpx.MockTransport(handler)
    real = httpx.Client

    def fake_client(**kwargs):
        return real(transport=transport)

    monkeypatch.setattr(providers.httpx, "Client", fake_client)
    monkeypatch.setattr(providers, "bitbucket_secret", lambda: ({"Authorization": "x"}, "token"))
    repo = providers.RemoteRepo(
        "bitbucket-server", "bb.example.com", "PROJ", "repo", "https://bb.example.com"
    )
    url = providers._post_bitbucket_server(repo, 1, "body")
    assert "pull-requests/1" in url
    assert json.loads(seen[0].content)["text"] == "body"


def test_pr_urls():
    assert providers._pr_url(providers.RemoteRepo("github", "github.com", "o", "r"), 1).endswith(
        "/pull/1"
    )
    assert "bitbucket.org/o/r" in providers._pr_url(
        providers.RemoteRepo("bitbucket-cloud", "bitbucket.org", "o", "r"), 1
    )
    assert "projects/PROJ/repos/r" in providers._pr_url(
        providers.RemoteRepo("bitbucket-server", "bb", "PROJ", "r", "https://bb"), 1
    )


def test_fetch_pr_diff_requires_supported_remote(monkeypatch):
    monkeypatch.setattr(providers, "detect_repo", lambda root: None)
    import pytest

    with pytest.raises(providers.ProviderError):
        providers.fetch_pr_diff("/tmp", 1)
