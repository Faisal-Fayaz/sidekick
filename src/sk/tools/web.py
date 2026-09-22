"""Tools submodule: SSRF guard + read_url / web_search. (split from sk/tools.py, pure move)."""

from __future__ import annotations


def _url_blocked(url: str) -> str | None:
    """SSRF guard. Returns error or None if OK."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    try:
        u = urlparse(url.strip())
    except Exception:
        return "Error: bad URL."
    if u.scheme not in ("http", "https"):
        return "Error: only http/https allowed."
    host = (u.hostname or "").lower()
    if not host or len(url) > 2000:
        return "Error: bad URL."
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".internal"):
        return f"Error: blocked host '{host}'."
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return f"Error: blocked IP '{host}'."
    except ValueError:
        pass  # hostname, resolve below
    try:
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(5)
        try:
            resolved = socket.getaddrinfo(host, None)
        finally:
            socket.setdefaulttimeout(old_timeout)
        ips = {r[4][0] for r in resolved}
        for rip in ips:
            try:
                ip = ipaddress.ip_address(rip)
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                ):
                    return f"Error: host resolves to private IP ({rip})."
            except ValueError:
                pass
    except Exception:
        return "Error: DNS failed."
    return None


def _html_to_text(html: str, limit: int = 20000) -> tuple[str, str]:
    """Minimal readability: title + visible text. Stdlib only."""
    import re
    from html.parser import HTMLParser

    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()[:200]

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.out: list[str] = []
            self.skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "nav", "footer", "aside"):
                self.skip += 1
            if tag in ("p", "br", "h1", "h2", "h3", "h4", "li", "tr"):
                self.out.append("\n")

        def handle_endtag(self, tag):
            if tag in ("script", "style", "nav", "footer", "aside") and self.skip:
                self.skip -= 1

        def handle_data(self, data):
            if not self.skip:
                self.out.append(data)

    p = P()
    p.feed(html[:500_000])
    text = re.sub(r"[ \t]+", " ", "".join(p.out))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return (title, text[:limit])


def tool_web_search(query: str, count: int = 5) -> str:
    """Keyless web search via DuckDuckGo html endpoint. Returns title/url/snippet lines."""
    import re
    from urllib.parse import parse_qs, unquote, urlparse

    query = (query or "").strip()[:300]
    if not query:
        return "Error: empty query."
    count = max(1, min(int(count or 5), 8))
    try:
        import httpx

        with httpx.Client(timeout=20, follow_redirects=True) as c:
            r = c.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"},
            )
            r.raise_for_status()
            html = r.text
    except Exception as e:
        return f"Error searching: {str(e)[:200]}"
    # result links: <a class="result__a" href="//duckduckgo.com/l/?uddg=<url>&...">title</a>
    links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL)
    snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
    out: list[str] = []
    for i, (href, title) in enumerate(links[:count]):
        url = href
        if "uddg=" in href:
            try:
                q = parse_qs(urlparse("https:" + href if href.startswith("//") else href).query)
                url = unquote(q.get("uddg", [href])[0])
            except Exception:
                pass
        elif href.startswith("//"):
            url = "https:" + href
        title = re.sub(r"<[^>]+>", "", title or "").strip()[:120]
        snip = re.sub(r"<[^>]+>", "", snips[i] if i < len(snips) else "").strip()[:200]
        out.append(f"{i + 1}. {title}\n   {url}" + (f"\n   {snip}" if snip else ""))
    return "\n".join(out) if out else "(no results — try different words)"


def tool_read_url(url: str, max_chars: int = 6000) -> str:
    blocked = _url_blocked(url)
    if blocked:
        return blocked
    max_chars = max(500, min(int(max_chars or 6000), 15000))
    try:
        import httpx

        with httpx.Client(timeout=20, follow_redirects=True, max_redirects=3) as c:
            r = c.get(url.strip(), headers={"User-Agent": "sidekick/0.1"})
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if (
                "text" not in ctype
                and "html" not in ctype
                and "json" not in ctype
                and "xml" not in ctype
            ):
                return f"Error: unsupported content-type '{ctype}'."
            raw = r.text
    except Exception as e:
        return f"Error fetching: {str(e)[:300]}"
    if len(raw) > 1_000_000:
        return "Error: page too large (>1MB)."
    title, text = _html_to_text(raw)
    if len(text) < 50:
        text = raw[:max_chars]
    else:
        text = text[:max_chars]
    head = f"# {title}\n" if title else ""
    tail = "\n... [truncated]" if len(raw) > max_chars else ""
    return f"{head}{text}{tail}"
