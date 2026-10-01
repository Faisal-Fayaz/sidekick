"""JS-capable extraction tests (fixes #182): JSON-LD, __NEXT_DATA__, meta
fallbacks, pipeline wiring with mocked HTTP. Fully offline."""

import pytest

from sk.tools.web import extract_embedded_text, tool_read_url

# These tests fake HTTP to exercise redirect/SSRF logic; they need the
# egress policy to say yes to the public decoy hosts. Deny-by-default
# (#328) would otherwise short-circuit every case here.
pytestmark = pytest.mark.usefixtures("egress_test_hosts")


NEXT_PAGE = """<html><head><title>Blog</title>
<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {
"title": "Why local models win",
"body": "Local inference keeps your code on your machine and answers in milliseconds once warm."}}}</script>
</head><body><div id="__next"></div><script src="/app.js"></script></body></html>"""

LD_PAGE = """<html><head><title>News</title>
<script type="application/ld+json">{"@type": "NewsArticle",
"headline": "Sidekick ships checkpoints",
"articleBody": "Every file edit is snapshotted so bad turns can be rewound.",
"author": {"name": "Ed", "url": "https://x/y"}}</script>
</head><body><div id="root"></div></body></html>"""

META_PAGE = """<html><head>
<meta name="description" content="A fast local-first coding companion.">
</head><body><div id="app"></div></body></html>"""


def test_next_data_extracts():
    out = extract_embedded_text(NEXT_PAGE)
    assert "Why local models win" in out
    assert "keeps your code on your machine" in out


def test_json_ld_extracts_skips_noise():
    out = extract_embedded_text(LD_PAGE)
    assert "Sidekick ships checkpoints" in out
    assert "snapshotted so bad turns" in out
    assert "https://x/y" not in out


def test_meta_fallback():
    out = extract_embedded_text(META_PAGE)
    assert "fast local-first coding companion" in out


def test_garbage_json_skipped():
    html = '<script type="application/json">not json {{{</script><p>hi</p>'
    assert extract_embedded_text(html) == ""


def _fake_client_factory(html, ctype="text/html"):
    class Resp:
        status_code = 200
        headers = {"content-type": ctype}
        text = html

        def raise_for_status(self):
            pass

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return Resp()

    return Client


def test_tool_uses_embedded_when_visible_empty(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "Client", _fake_client_factory(NEXT_PAGE))
    out = tool_read_url("https://example.com/post", max_chars=6000)
    assert "keeps your code on your machine" in out
    assert "[rendered from embedded page data]" in out


def test_tool_prefers_visible_text(monkeypatch):
    import httpx

    html = "<html><body><p>" + ("visible words " * 20) + "</p></body></html>"
    monkeypatch.setattr(httpx, "Client", _fake_client_factory(html))
    out = tool_read_url("https://example.com/p", max_chars=6000)
    assert "visible words" in out
    assert "[rendered from embedded page data]" not in out


def test_tool_empty_still_falls_back_raw(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "Client", _fake_client_factory("<html><body></body></html>"))
    out = tool_read_url("https://example.com/empty", max_chars=6000)
    assert "[rendered from embedded page data]" not in out


def test_ssrf_and_caps_unchanged(monkeypatch):
    import httpx

    assert "blocked" in tool_read_url("http://127.0.0.1:9/").lower()
    monkeypatch.setattr(httpx, "Client", _fake_client_factory("x" * 1_000_001))
    assert "too large" in tool_read_url("https://example.com/big")
