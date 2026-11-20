"""Runtime helper for `sk verify-browser` when Playwright is installed (#520).

Playwright is imported lazily here — this module is only executed by the
optional pack, so importing Sidekick never requires it.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--screenshot", default="")
    parser.add_argument("--emit-test", default="")
    args = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright  # optional, runtime only

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(args.url, wait_until="domcontentloaded")
        if args.snapshot:
            try:
                text = page.accessibility.snapshot()
            except Exception:
                text = page.locator("body").inner_text()
            Path(args.snapshot).expanduser().write_text(str(text), encoding="utf-8")
        if args.screenshot:
            Path(args.screenshot).expanduser().parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(args.screenshot).expanduser()))
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
