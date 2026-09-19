"""Web fetch tests: guard rails offline + parsing; one live check is opt-in."""

from sk.tools import _html_to_text, _url_blocked, dispatch_tool, tool_read_url


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
