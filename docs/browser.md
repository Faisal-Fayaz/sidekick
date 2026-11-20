# Browser verification (optional pack)

Review covers a diff; browser verification covers what a page actually renders.
This pack uses a browser **already installed** on the machine. It adds no
required dependency and does not download a browser on first use.

```bash
sk verify-browser http://127.0.0.1:5173 \
  --snapshot /tmp/snapshot.txt \
  --screenshot /tmp/page.png \
  --emit-test /tmp/test_page.py

sk verify-browser https://example.com --json
```

If no Chromium/Chrome binary or Python Playwright install is found, the command
prints `No browser or Playwright install detected; nothing was launched.` and
exits **0**. It never installs anything and never turns a missing optional tool
into a failed run.

## Detection order

1. `import playwright` (Python Playwright) — uses `python -m sk.browser_probe`,
   which captures the accessibility tree and an optional screenshot.
2. `google-chrome`, `google-chrome-stable`, `chromium` or `chromium-browser` on
   `PATH` — uses `--headless=new --dump-dom` and can take a screenshot. A plain
   Chrome binary cannot produce an accessibility tree, so the result says the
   capture is a DOM dump rather than claiming otherwise.
3. Nothing found → `available: false`, no launch.

## Security

The target URL passes **both** gates before a browser starts:

- `sk egress` — deny by default; allow a local dev server explicitly with
  `sk egress allow 127.0.0.1`.
- the twice-asked SSRF guard — a private/loopback IP is refused even when the
  egress list allows it. A refusal names the gate and no subprocess runs.

Every fetch decision is recorded in the audit ledger under
`egress:browser:verify`. There is no separate network path for this pack.

**Residual risk, stated plainly:** the browser can navigate itself (a link, a
redirect, JavaScript) after the initial check. Those in-page navigations are
**not** re-gated. Use a disposable profile and a local dev server you trust;
this pack verifies rendering, it is not a sandbox for hostile pages.

## Generated e2e test

`--emit-test FILE` writes a short, editable Playwright test skeleton that
navigates to the verified URL and asserts a title. It is a starting point for a
real test, not a verified test in its own right. The generated file is valid
Python and contains a comment saying to edit it before committing.

## Result shape

```json
{
  "schema": 1,
  "target": "https://example.com",
  "available": true,
  "ok": true,
  "browser": "playwright",
  "snapshot": "/tmp/snapshot.txt",
  "screenshot": "",
  "test": "",
  "note": ""
}
```

## Tests

`tests/test_browser_verify.py` (11 cases) uses an injected runner, so no real
browser is needed: detection, no-browser behaviour, blocked URLs never
launching, both command shapes, artifact writing, generated test validity,
error reporting and both CLI paths. A real-browser test is not required for the
suite; the pack is optional by design.

## See also

- [Egress policy](egress.md) — the allow list that gates the target URL.
- [Threat model](threat-model.md) — the trust boundary a rendered page crosses.
- [Checks](checks.md) — the code-level checks this pack complements.
