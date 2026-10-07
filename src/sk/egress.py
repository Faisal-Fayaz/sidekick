"""Egress policy: what the *model* is allowed to make us fetch (#328).

The distinction that makes this tractable:

* **Operator-configured traffic** — the model API endpoint, MCP servers. The user
  typed these into their own config and supplied a key. Always allowed.
* **Model-chosen traffic** — a page the model decided to read, a search, a URL a
  provider handed back for an image. These are the destinations an attacker
  reaches through prompt injection.

This module governs the second category only, and the answer is **deny by
default**: an empty allowlist means no network. Every decision, allow or deny,
is written to the ledger so `sk egress` and `sk audit` have something real to
show.

## The ceiling, stated rather than hidden

This covers fetches made **through the agent's tools**. A `shell` command running
`curl` is not covered — a shell string denylist cannot be a network boundary
(#288, and the reason #329 exists). The README states this explicitly so the
control is not oversold.

Fence markers and SSRF checks run *in addition* to this: allowlisted does not
mean private-IP-safe, and vice versa.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# Human-readable reason strings, kept short so they fit a tool result.
REASON_NO_ALLOWLIST = "egress allowlist is empty"
REASON_NOT_ALLOWED = "host is not in the egress allowlist"
REASON_BAD_ENTRY = "allowlist entry is too broad to honour"
REASON_BAD_URL = "could not parse destination"

# One-shot keys for ledger-write warnings, so a broken audit DB does not print
# on every refused fetch.
_ledger_warned: set[str] = set()


def _host_of(url: str) -> str:
    try:
        return (urlsplit(str(url)).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def host_allowed(host: str, allow: tuple[str, ...]) -> bool:
    """Exact match, or `*.suffix` covering the host and its subdomains."""
    h = (host or "").lower().rstrip(".")
    if not h or not allow:
        return False
    for entry in allow:
        e = str(entry).strip().lower().rstrip(".")
        if not e or e in ("*", "**"):
            continue  # refused at parse time; never honoured here either
        if e.startswith("*."):
            suffix = e[1:]  # ".example.com"
            if h.endswith(suffix) and len(h) > len(suffix):
                return True
        elif h == e:
            return True
    return False


def check(url: str, allow: tuple[str, ...] | None = None) -> str | None:
    """Return None if the destination may be fetched, else the reason.

    Reads the allowlist from config when not supplied, so the common case needs
    no plumbing. Never raises.
    """
    try:
        host = _host_of(url)
        if not host:
            return REASON_BAD_URL
        if allow is None:
            allow = _current_allow()
        if not allow:
            return REASON_NO_ALLOWLIST
        # A bare `*` / `**` is refused at parse time and never honoured by
        # host_allowed, so without this the user was told the host "is not in the
        # egress allowlist" while their allowlist held exactly that entry. Name
        # the actual problem instead.
        if any(str(a).strip().lower().rstrip(".") in ("*", "**") for a in allow):
            return REASON_BAD_ENTRY
        if host_allowed(host, allow):
            return None
        return f"{REASON_NOT_ALLOWED} ({host})"
    except Exception:
        # Fail closed: an unreadable policy must not become a free pass.
        return REASON_BAD_URL


def _current_allow() -> tuple[str, ...]:
    try:
        from .config import Config

        return tuple(Config.load().egress_allow or ())
    except Exception:
        return ()


def hint(url_or_host: str) -> str:
    """Actionable next step, so a denial teaches the user the command.

    Accepts a full URL or a bare host; the allowlist is keyed on host, so the
    hint must name the host the user would actually add.
    """
    raw = str(url_or_host or "").strip()
    h = _host_of(raw) or raw.lower()
    if not h:
        return "sk egress allow <host>"
    return f"sk egress allow {h}"


def record(tool: str, url: str, reason: str | None, session: str = "") -> None:
    """Write the decision to the ledger. Never raises.

    Denials are the valuable rows: an `egress` row with ok=0 can only exist if
    something actually tried to leave, which is what makes `sk audit --prove`
    (#156) say something it can back up.

    So a failure to write is reported rather than swallowed. Losing a row here is
    not merely a lost log line -- for a *denial* it removes the only evidence
    that anything was ever blocked, and `sk audit --prove` cannot tell "the
    policy held" from "we never tried". Deduped, so a broken ledger warns once
    rather than on every refused fetch.
    """
    try:
        from .store import log_egress

        log_egress(tool or "fetch", _host_of(url), str(url or "")[:400], reason, session=session)
    except Exception as e:
        import sys

        msg = (
            f"could not write the egress audit row for {_host_of(url) or url} ({type(e).__name__})"
        )
        key = msg[:160]
        if key not in _ledger_warned:
            _ledger_warned.add(key)
            try:
                print(f"[sidekick] warning: {msg}", file=sys.stderr)
            except Exception:
                pass
