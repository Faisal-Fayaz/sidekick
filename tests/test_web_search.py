"""Pluggable search providers (#492).

Fakes stand in for Serper/Brave/SearXNG/DuckDuckGo, so no test needs a key or
the network. The result guard and the extraction are exercised on local data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import sk.search as search
from sk.tools import _html_to_text
from sk.tools.web import tool_web_search


class Response:
    def __init__(self, payload=None, text="", status=200):
        self.payload = payload
        self.text = text
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"http {self.status}")

    def json(self):
        return self.payload


DDG_HTML = """
<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Alpha</a>
<a class="result__snippet">first hit</a>
<a class="result__a" href="https://example.org/b">Beta</a>
<a class="result__snippet">second hit</a>
</body></html>
"""


def _blocked(url: str) -> str | None:
    return "blocked" if "internal" in url else None


@pytest.fixture
def calls():
    return []


@pytest.fixture
def fetch(calls):
    def fake(url, headers, **kw):
        calls.append({"url": url, "headers": headers, **kw})
        if "duckduckgo" in url:
            return Response(text=DDG_HTML), ""
        if "serper" in url:
            return (
                Response(
                    {
                        "organic": [
                            {
                                "title": "Serper hit",
                                "link": "https://example.com/s",
                                "snippet": "via key",
                            }
                        ]
                    }
                ),
                "",
            )
        if "brave" in url:
            return (
                Response(
                    {
                        "web": {
                            "results": [
                                {
                                    "title": "Brave hit",
                                    "url": "https://example.com/b",
                                    "description": "brave",
                                }
                            ]
                        }
                    }
                ),
                "",
            )
        if "searxng" in url:
            return (
                Response(
                    {
                        "results": [
                            {
                                "title": "Local hit",
                                "url": "https://example.com/x",
                                "content": "local",
                            }
                        ]
                    }
                ),
                "",
            )
        return Response({}), ""

    return fake


def _cfg(provider="ddg", key="", searxng_url=""):
    return {"provider": provider, "key": key, "searxng_url": searxng_url}


# --- the default stays keyless ---------------------------------------------


def test_ddg_is_the_default_and_needs_no_key(fetch, calls):
    results, note = search.search("two words", 5, fetch=fetch, blocked=_blocked, config=_cfg())
    assert note == ""
    assert [r.title for r in results] == ["Alpha", "Beta"]
    assert results[0].url == "https://example.com/a"
    assert results[0].snippet == "first hit"
    assert calls[0]["params"] == {"q": "two words"}
    assert calls[0].get("method", "GET") == "GET"


# --- each keyed provider ----------------------------------------------------


def test_serper_posts_the_key_and_parses_organic(fetch, calls):
    results, note = search.search(
        "q", 5, fetch=fetch, blocked=_blocked, config=_cfg("serper", key="K")
    )
    assert note == "" and [r.title for r in results] == ["Serper hit"]
    assert calls[0]["method"] == "POST"
    assert calls[0]["headers"]["X-API-KEY"] == "K"
    assert calls[0]["json_body"] == {"q": "q", "num": 5}


def test_brave_sends_the_subscription_token_and_parses_results(fetch, calls):
    results, _ = search.search("q", 5, fetch=fetch, blocked=_blocked, config=_cfg("brave", key="K"))
    assert [r.title for r in results] == ["Brave hit"]
    assert calls[0]["headers"]["X-Subscription-Token"] == "K"
    assert calls[0]["params"] == {"q": "q", "count": 5}


def test_searxng_needs_a_url_and_reads_json(fetch, calls):
    _, err = search.search("q", 5, fetch=fetch, blocked=_blocked, config=_cfg("searxng"))
    assert "searxng_url" in err and not calls
    results, _ = search.search(
        "q",
        5,
        fetch=fetch,
        blocked=_blocked,
        config=_cfg("searxng", searxng_url="http://searxng.local"),
    )
    assert [r.title for r in results] == ["Local hit"]
    assert calls[0]["params"] == {"q": "q", "format": "json", "count": 5}


@pytest.mark.parametrize("provider", ["serper", "brave"])
def test_a_missing_key_fails_clearly(provider, fetch, calls):
    results, err = search.search("q", 5, fetch=fetch, blocked=_blocked, config=_cfg(provider))
    assert results == []
    assert "needs a key" in err
    assert not calls, "no request was made without a key"


def test_an_unknown_provider_is_named(fetch):
    results, err = search.search("q", 5, fetch=fetch, blocked=_blocked, config=_cfg("yahoo"))
    assert results == []
    assert "yahoo" in err and "ddg" in err


def test_a_provider_error_is_returned(calls):
    def broken(url, headers, **kw):
        return None, "Error searching: upstream down"

    results, err = search.search("q", 5, fetch=broken, blocked=_blocked, config=_cfg())
    assert results == [] and "upstream down" in err


# --- results are filtered ---------------------------------------------------


def test_a_result_pointing_inward_is_dropped(fetch):
    def blocked(url):
        return "blocked" if "internal" in url else None

    def inward(url, headers, **kw):
        return (
            Response(
                {"organic": [{"title": "Bad", "link": "http://internal.test/x", "snippet": ""}]}
            ),
            "",
        )

    results, note = search.search(
        "q", 5, fetch=inward, blocked=blocked, config=_cfg("serper", key="K")
    )
    assert results == []
    assert "1 result(s) dropped" in note


# --- settings are global only ----------------------------------------------


def test_settings_read_the_global_file_and_env(tmp_path, monkeypatch):
    from sk import config as config_mod

    global_file = tmp_path / "config.toml"
    global_file.write_text(
        'web_search_provider = "brave"\nweb_search_key = "from-file"\nsearxng_url = "http://file"\n'
    )
    monkeypatch.setattr(config_mod, "CONFIG_PATH", global_file)
    monkeypatch.delenv("SIDEKICK_WEB_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("SIDEKICK_WEB_SEARCH_KEY", raising=False)
    monkeypatch.delenv("SIDEKICK_SEARXNG_URL", raising=False)
    assert search.settings() == {
        "provider": "brave",
        "key": "from-file",
        "searxng_url": "http://file",
    }

    monkeypatch.setenv("SIDEKICK_WEB_SEARCH_PROVIDER", "serper")
    monkeypatch.setenv("SIDEKICK_WEB_SEARCH_KEY", "from-env")
    assert search.settings()["provider"] == "serper"
    assert search.settings()["key"] == "from-env"


def test_a_project_file_cannot_choose_a_provider(tmp_path, monkeypatch):
    from sk import config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "missing.toml")
    project = tmp_path / ".sidekick.toml"
    project.write_text('web_search_provider = "serper"\nweb_search_key = "stolen"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SIDEKICK_WEB_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("SIDEKICK_WEB_SEARCH_KEY", raising=False)
    cfg = search.settings()
    assert cfg["provider"] == "ddg" and cfg["key"] == ""


# --- the CLI tool surface still formats the same way -----------------------


def test_tool_web_search_uses_the_configured_provider(monkeypatch):
    monkeypatch.setattr(search, "settings", lambda: _cfg("serper", key="K"))
    monkeypatch.setattr(
        "sk.tools.web._fetch_with_redirects",
        lambda url, headers, **kw: (
            Response(
                {"organic": [{"title": "Hit", "link": "https://example.com/h", "snippet": "text"}]}
            ),
            "",
        ),
    )
    monkeypatch.setattr("sk.tools.web._url_blocked_twice", lambda url: None)
    out = tool_web_search("q")
    assert "1. Hit" in out and "https://example.com/h" in out and "text" in out


def test_tool_web_search_reports_a_missing_key(monkeypatch):
    monkeypatch.setattr(search, "settings", lambda: _cfg("brave"))
    out = tool_web_search("q")
    assert "needs a key" in out


# --- page extraction --------------------------------------------------------


def test_extraction_keeps_the_article_and_drops_the_chrome():
    html = """
    <html><head><title>Story</title><script>cookie alert</script></head><body>
      <nav class="navigation">Home About</nav>
      <div id="cookie-banner">Accept cookies</div>
      <article>
        <h1>The headline</h1>
        <p>The first paragraph with real content.</p>
        <p>The second paragraph with more content.</p>
      </article>
      <footer>Copyright</footer>
    </body></html>
    """
    title, text = _html_to_text(html)
    assert title == "Story"
    assert "# The headline" in text
    assert "first paragraph" in text and "second paragraph" in text
    for chrome in ("Home About", "Accept cookies", "cookie alert", "Copyright"):
        assert chrome not in text


def test_extraction_without_an_article_falls_back_to_the_body():
    _, text = _html_to_text("<html><body><p>Plain page body.</p></body></html>")
    assert "Plain page body." in text
