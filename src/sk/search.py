"""Pluggable web search providers (#492).

One seam, four providers. DuckDuckGo's HTML endpoint stays the keyless default;
Serper and Brave use a key the *user* supplies in the global config or the
process environment; SearXNG is a local, keyless endpoint. A repository cannot
set any of these values.

This module has no network code of its own: the caller injects the fetch and
the URL guard, so every provider goes through the same egress policy, the same
redirect checks and the same SSRF filter as `read_url`. Transport failures are
returned as messages, never raised.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

PROVIDERS = ("ddg", "serper", "brave", "searxng")
DDG_URL = "https://html.duckduckgo.com/html/"
SERPER_URL = "https://google.serper.dev/search"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

Fetch = Callable[..., tuple[Any, str]]
Blocked = Callable[[str], str | None]


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


def settings() -> dict[str, str]:
    """Provider, key and SearXNG URL from global config or environment.

    The global file is read directly rather than through `Config.load()`: a
    project file must not be able to choose a provider or supply a key, and the
    model-driven search tool has no Config in its signature.
    """
    from .config import CONFIG_PATH

    vals: dict[str, Any] = {}
    try:
        import tomllib

        with open(CONFIG_PATH, "rb") as handle:
            loaded = tomllib.load(handle)
        if isinstance(loaded, dict):
            vals = loaded
    except Exception:
        pass
    return {
        "provider": (
            os.getenv("SIDEKICK_WEB_SEARCH_PROVIDER", "").strip()
            or str(vals.get("web_search_provider", "ddg") or "ddg")
        )
        .strip()
        .lower(),
        "key": os.getenv("SIDEKICK_WEB_SEARCH_KEY", "").strip()
        or str(vals.get("web_search_key", "") or ""),
        "searxng_url": os.getenv("SIDEKICK_SEARXNG_URL", "").strip()
        or str(vals.get("searxng_url", "") or ""),
    }


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(value or ""))).strip()


def parse_ddg(html: str, count: int) -> list[SearchResult]:
    """Parse DuckDuckGo's html endpoint. Keyless, and the existing default."""
    links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL)
    snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
    out: list[SearchResult] = []
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
        out.append(
            SearchResult(
                _text(title)[:120],
                url,
                _text(snips[i] if i < len(snips) else "")[:200],
            )
        )
    return out


def _json_response(response: Any, context: str) -> tuple[dict, str]:
    try:
        response.raise_for_status()
        data = response.json()
        return (data if isinstance(data, dict) else {}), ""
    except Exception as e:
        return {}, f"Error {context}: {str(e)[:200]}"


def search(
    query: str,
    count: int,
    *,
    fetch: Fetch,
    blocked: Blocked,
    config: dict[str, str] | None = None,
) -> tuple[list[SearchResult], str]:
    """Run a provider and filter its results through the URL guard.

    Returns (results, note). `note` is a user-facing message for a refusal or a
    partial result — a dropped result never becomes an answer the model can
    follow blindly.
    """
    cfg = config if config is not None else settings()
    provider = cfg.get("provider", "ddg")
    key = cfg.get("key", "")
    searxng = cfg.get("searxng_url", "")

    results: list[SearchResult] = []
    if provider == "ddg":
        response, err = fetch(
            DDG_URL,
            {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"},
            timeout=20,
            params={"q": query},
            context="searching",
        )
        if err:
            return [], err
        try:
            response.raise_for_status()
            results = parse_ddg(response.text, count)
        except Exception as e:
            return [], f"Error searching: {str(e)[:200]}"
    elif provider == "serper":
        if not key:
            return [], "Error: Serper needs a key. Set web_search_key or SIDEKICK_WEB_SEARCH_KEY."
        response, err = fetch(
            SERPER_URL,
            {"X-API-KEY": key, "Content-Type": "application/json"},
            timeout=20,
            json_body={"q": query, "num": count},
            method="POST",
            context="searching Serper",
        )
        if err:
            return [], err
        data, err = _json_response(response, "searching Serper")
        if err:
            return [], err
        for hit in (data.get("organic") or [])[:count]:
            results.append(
                SearchResult(
                    _text(hit.get("title"))[:120],
                    str(hit.get("link") or ""),
                    _text(hit.get("snippet"))[:200],
                )
            )
    elif provider == "brave":
        if not key:
            return [], "Error: Brave needs a key. Set web_search_key or SIDEKICK_WEB_SEARCH_KEY."
        response, err = fetch(
            BRAVE_URL,
            {"X-Subscription-Token": key, "Accept": "application/json"},
            timeout=20,
            params={"q": query, "count": count},
            context="searching Brave",
        )
        if err:
            return [], err
        data, err = _json_response(response, "searching Brave")
        if err:
            return [], err
        for hit in ((data.get("web") or {}).get("results") or [])[:count]:
            results.append(
                SearchResult(
                    _text(hit.get("title"))[:120],
                    str(hit.get("url") or ""),
                    _text(hit.get("description"))[:200],
                )
            )
    elif provider == "searxng":
        if not searxng:
            return [], "Error: SearXNG needs a URL. Set searxng_url or SIDEKICK_SEARXNG_URL."
        response, err = fetch(
            searxng,
            {"Accept": "application/json"},
            timeout=20,
            params={"q": query, "format": "json", "count": count},
            context="searching SearXNG",
            # An operator-configured local SearXNG is a legitimate target, like
            # a local MCP server. Result URLs still get the strict guard below.
            allow_loopback=True,
        )
        if err:
            return [], err
        data, err = _json_response(response, "searching SearXNG")
        if err:
            return [], err
        for hit in (data.get("results") or [])[:count]:
            results.append(
                SearchResult(
                    _text(hit.get("title"))[:120],
                    str(hit.get("url") or ""),
                    _text(hit.get("content"))[:200],
                )
            )
    else:
        return [], (
            f"Error: unknown web_search_provider {provider!r}. "
            f"Choose one of: {', '.join(PROVIDERS)}."
        )

    kept: list[SearchResult] = []
    dropped = 0
    for item in results:
        if blocked(item.url):
            dropped += 1
            continue
        kept.append(item)
    note = f"\n({dropped} result(s) dropped by the URL guard.)" if dropped else ""
    return kept, note
