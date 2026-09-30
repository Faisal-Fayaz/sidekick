"""Tools submodule: SSRF guard + read_url / web_search. (split from sk/tools.py, pure move)."""

from __future__ import annotations

from typing import Any


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


def _harvest_strings(obj, out: list, min_len: int = 24, cap: int = 8000) -> None:
    """Recursively collect long strings from decoded JSON. Noisy keys skipped;
    semantic keys (title/headline/...) get a lower bar. No dedupe beyond exact."""
    import re

    BOOST_KEYS = ("title", "headline", "name", "description", "articlebody", "text")
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in ("url", "image", "logo", "sameas", "@id", "@type"):
                if isinstance(v, str):
                    continue
            if isinstance(v, str) and str(k).lower() in BOOST_KEYS:
                s = re.sub(r"\s+", " ", v).strip()
                if len(s) >= 8 and s not in out:
                    out.append(s[:cap])
            else:
                _harvest_strings(v, out, min_len, cap)
    elif isinstance(obj, list):
        for v in obj:
            _harvest_strings(v, out, min_len, cap)
    elif isinstance(obj, str):
        s = re.sub(r"\s+", " ", obj).strip()
        if len(s) >= min_len and s not in out:
            out.append(s[:cap])


def extract_embedded_text(html: str, budget: int = 8000) -> str:
    """Content for JS-rendered pages: JSON-LD, __NEXT_DATA__, embedded JSON,
    meta/OG descriptions — the data SPAs ship for crawlers. Stdlib only."""
    import json
    import re
    from html import unescape

    parts: list[str] = []
    try:
        for m in re.finditer(
            r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
            html[:500_000],
            re.IGNORECASE | re.DOTALL,
        ):
            try:
                data = json.loads(m.group(1).strip())
            except Exception:
                continue
            _harvest_strings(data, parts)
        nxt = re.search(
            r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html[:500_000],
            re.IGNORECASE | re.DOTALL,
        )
        if nxt:
            try:
                _harvest_strings(json.loads(nxt.group(1).strip()), parts)
            except Exception:
                pass
        for m in re.finditer(
            r'<script[^>]+type="application/json"[^>]*>(.*?)</script>',
            html[:500_000],
            re.IGNORECASE | re.DOTALL,
        ):
            try:
                _harvest_strings(json.loads(m.group(1).strip()), parts)
            except Exception:
                continue
        for attr in (
            'property="og:description"',
            'property="og:description"',
            'name="description"',
            'name="description"',
        ):
            for m in re.finditer(
                r"<meta[^>]*" + attr + r"[^>]*content=\"([^\"]+)\"",
                html[:200_000],
                re.IGNORECASE,
            ):
                text = unescape(m.group(1)).strip()
                if text and text not in parts:
                    parts.append(text[:500])
    except Exception:
        pass
    return "\n\n".join(parts)[:budget]


def _fetch_with_redirects(
    url: str,
    headers: dict,
    timeout: int = 20,
    params: dict | None = None,
    context: str = "fetching",
) -> tuple[Any, str]:
    """GET with a manual redirect chain: initial URL + every hop re-validated
    against the SSRF guard (httpx auto-follow would fetch targets unchecked).

    Returns (response, "") or (None, error). params go on the first request
    only; redirect hops carry their own URLs. Never raises.
    """
    from urllib.parse import urljoin

    try:
        import httpx
    except Exception as e:
        return None, f"Error {context}: {e}"
    blocked = _url_blocked(url)
    if blocked:
        return None, blocked
    try:
        current = url.strip()
        r = None
        with httpx.Client(timeout=timeout, follow_redirects=False) as c:
            for i in range(4):  # initial fetch + up to 3 hops
                kw = {"headers": headers}
                if params and i == 0:
                    kw["params"] = params
                r = c.get(current, **kw)
                if r.status_code not in (301, 302, 303, 307, 308):
                    break
                loc = (r.headers.get("location") or "").strip()
                if not loc:
                    break
                current = urljoin(current, loc)
                blocked = _url_blocked(current)
                if blocked:
                    return None, f"Error: redirect to blocked URL: {current}"
            else:
                return None, "Error: too many redirects (max 3)."
        if r is None:
            return None, "Error: fetch failed."
        return r, ""
    except Exception as e:
        return None, f"Error {context}: {str(e)[:300]}"


def tool_web_search(query: str, count: int = 5) -> str:
    """Keyless web search via DuckDuckGo html endpoint. Returns title/url/snippet lines."""
    import re
    from urllib.parse import parse_qs, unquote, urlparse

    query = (query or "").strip()[:300]
    if not query:
        return "Error: empty query."
    count = max(1, min(int(count or 5), 8))
    r, err = _fetch_with_redirects(
        "https://html.duckduckgo.com/html/",
        {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"},
        timeout=20,
        params={"q": query},
        context="searching",
    )
    if err:
        return err
    try:
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
    max_chars = max(500, min(int(max_chars or 6000), 15000))
    r, err = _fetch_with_redirects(url, {"User-Agent": "sidekick/0.1"})
    if err:
        return err
    try:
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
        embedded = extract_embedded_text(raw, budget=max_chars)
        if len(embedded) >= 50:
            text = "[rendered from embedded page data]\n" + embedded
        else:
            text = raw[:max_chars]
    else:
        text = text[:max_chars]
    head = f"# {title}\n" if title else ""
    tail = "\n... [truncated]" if len(raw) > max_chars else ""
    return f"{head}{text}{tail}"
