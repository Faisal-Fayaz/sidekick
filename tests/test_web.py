"""Web fetch tests: guard rails offline + parsing; one live check is opt-in."""

from sk.agent import _auto_web_context
from sk.tools import _html_to_text, _url_blocked, dispatch_tool, tool_read_url, tool_web_search


def test_block_private():
    assert "blocked" in (_url_blocked("http://localhost:11434/") or "").lower()
    assert "blocked" in (_url_blocked("http://127.0.0.1/") or "").lower()
    assert "blocked" in (_url_blocked("http://169.254.169.254/") or "").lower()
    assert "only http" in (_url_blocked("ftp://x/") or "").lower()


def test_block_bad():
    assert _url_blocked("not a url") is not None


def test_html_strip():
    html = "<html><head><title>Hi</title></head><body><script>no</script><p>Yes <b>ok</b></p></body></html>"
    title, text = _html_to_text(html)
    assert title == "Hi" and "Yes ok" in text and "no" not in text


def test_dispatch_blocks():
    out = dispatch_tool("read_url", {"url": "http://127.0.0.1:11434/"})
    assert "blocked" in out.lower() or "error" in out.lower()


def test_auto_web_empty():
    assert _auto_web_context("no links here, just text") == ""


def test_auto_web_blocked_offline():
    out = _auto_web_context("check http://127.0.0.1:11434/ please")
    assert "127.0.0.1" in out and "blocked" in out.lower()


DDG_URL = "https://html.duckduckgo.com/html/"

RESULT_HTML = (
    "<html><body>"
    '<a class="result__a" href="https://example.com/a">Alpha result</a>'
    '<a class="result__snippet" href="#">alpha snip here</a>'
    "</body></html>"
)


class _SearchResp:
    def __init__(self, status=200, location=None, text=""):
        self.status_code = status
        self.headers = {"content-type": "text/html"}
        if location:
            self.headers["location"] = location
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _SearchChainClient:
    """Fake httpx.Client for web_search: scripted hops, records every fetch."""

    mode = "ok"
    instances = []

    def __init__(self, *a, **k):
        assert k.get("follow_redirects") is False  # #266: never auto-follow
        self.hops = []
        _SearchChainClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kw):
        self.hops.append((url, kw))
        if self.mode == "evil-redirect":
            if url == DDG_URL:
                return _SearchResp(302, location="http://127.0.0.1:9999/evil")
            raise AssertionError(f"must never fetch {url}")
        if self.mode == "ok-redirect":
            if url == DDG_URL:
                return _SearchResp(302, location="https://example.com/results")
            return _SearchResp(200, text=RESULT_HTML)
        if self.mode == "loop":
            return _SearchResp(302, location=DDG_URL + "?x=1")
        return _SearchResp(200, text=RESULT_HTML)


def _iso_search(monkeypatch, mode):
    from sk.tools import web as webmod

    _SearchChainClient.instances.clear()
    _SearchChainClient.mode = mode
    real_blocked = webmod._url_blocked

    def _allow_decoys(url):
        if "duckduckgo.com" in url or "example.com" in url:
            return None  # public decoys: skip real DNS so tests stay offline
        return real_blocked(url)

    monkeypatch.setattr(webmod, "_url_blocked", _allow_decoys)
    monkeypatch.setattr("httpx.Client", _SearchChainClient)


def test_search_redirect_to_private_never_fetched(monkeypatch):
    """#266: a redirect hop to an internal host is refused, not followed."""
    _iso_search(monkeypatch, "evil-redirect")
    out = tool_web_search("sidekick")
    assert "redirect to blocked URL" in out
    hops = _SearchChainClient.instances[-1].hops
    assert [h[0] for h in hops] == [DDG_URL]
    assert hops[0][1].get("params") == {"q": "sidekick"}


def test_search_benign_redirect_chain_works(monkeypatch):
    """#266: public redirect hops still resolve; params stay on hop one."""
    _iso_search(monkeypatch, "ok-redirect")
    out = tool_web_search("sidekick")
    assert "Alpha result" in out and "https://example.com/a" in out
    hops = _SearchChainClient.instances[-1].hops
    assert [h[0] for h in hops] == [DDG_URL, "https://example.com/results"]
    assert "params" not in hops[1][1]


def test_search_redirect_loop_capped(monkeypatch):
    """#266: endless redirects stop at the cap instead of hanging."""
    _iso_search(monkeypatch, "loop")
    out = tool_web_search("sidekick")
    assert "too many redirects" in out
    assert len(_SearchChainClient.instances[-1].hops) == 4
