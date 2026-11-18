# Web search and page reading

Sidekick's `web_search` tool has one provider seam and four backends. The
keyless DuckDuckGo HTML endpoint stays the default, so an existing install
behaves exactly as before. A user who already pays for Serper or Brave, or runs
SearXNG locally, can use it without a second search implementation.

## Providers

| Provider | `web_search_provider` | Credential | Endpoint |
|---|---|---|---|
| DuckDuckGo (default) | `ddg` | none | `https://html.duckduckgo.com/html/` |
| Serper | `serper` | `web_search_key` | `https://google.serper.dev/search` |
| Brave | `brave` | `web_search_key` | `https://api.search.brave.com/res/v1/web/search` |
| SearXNG | `searxng` | none | your `searxng_url` |

Set the values in `~/.sidekick/config.toml`:

```toml
web_search_provider = "brave"
web_search_key = "your-key-here"
```

Or in the process environment, which wins over the file:

```bash
export SIDEKICK_WEB_SEARCH_PROVIDER=serper
export SIDEKICK_WEB_SEARCH_KEY=your-key-here
```

For a local SearXNG:

```toml
web_search_provider = "searxng"
searxng_url = "http://127.0.0.1:8080/search"
```

or `SIDEKICK_SEARXNG_URL`.

**These settings are global only.** They are read from the user's config file
and the environment — never from `.sidekick.toml` in a repository. A cloned
project cannot swap your search provider or hand it a key.

A missing key is an error, not a silent fallback:

```text
Error: Serper needs a key. Set web_search_key or SIDEKICK_WEB_SEARCH_KEY.
```

An unknown provider is named, with the valid choices listed.

## Egress still governs the provider

Search results are data; the daemon does not open them during a search. When
the agent later calls `read_url` on a result, that fetch goes through the
ordinary gates: the egress allow list first, then the SSRF guard twice, on the
initial request and **on every redirect hop**. A result URL that points at an
internal address is dropped from the search output with a note, and a redirect
to one is refused rather than followed.

The provider's own endpoint is a fetch too, so allow it:

```bash
sk egress allow google.serper.dev      # Serper
sk egress allow api.search.brave.com   # Brave
sk egress allow 127.0.0.1              # local SearXNG
```

A local SearXNG endpoint is the one exception to the loopback SSRF rule — it is
operator-configured, like a local MCP server. **Result URLs never get that
exception**, even when the provider is local.

## Page extraction

`read_url` used to return most of the page. It now keeps the article and drops
the chrome:

- `<article>` or `role="main"` wins when present; otherwise the whole body is used.
- `script`, `style`, `nav`, `header`, `footer`, `aside`, `form`, `button`, `iframe`,
  `svg` and similar tags are skipped, as are elements whose class/id/role hints
  at cookies, banners, ads, sidebars or navigation.
- Headings keep a markdown `#` marker so the model can see the structure.
- The result is still capped (6,000 characters by default, 15,000 maximum), and
  the embedded-JSON fallback remains for pages that render client-side.

No new runtime dependency: this is `html.parser` plus a few patterns, the same
stdlib-only approach as the original extractor.

## Tests

`tests/test_web_search.py` fakes all four providers and checks keys, request shape,
result filtering, global-only settings, and extraction against an HTML fixture.
The existing redirect/SSRF tests in `tests/test_web.py` still apply to every
provider because the fetch function is injected from the tool layer.

## See also

- [Egress policy](egress.md) — what the model may fetch, and what it was blocked from.
- [The SDK and the local API](sdk.md) — other ways into the same core.
