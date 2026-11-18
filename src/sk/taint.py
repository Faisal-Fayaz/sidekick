"""Provenance that survives from a fenced block to the tool gate (#506).

`trust.fence` tells the *model* that a block is data. This module tells the
*gate* which tool arguments came from such a block, so a page or a repo file
cannot quietly become a shell command or a fetch. It is the runtime complement
to the advisory fence described in `docs/trust-model` — prompt framing raises
the floor; this is a deny rule.

Per-turn marks live in a ContextVar, so a concurrent local API turn cannot
inherit another turn's taint. The marks are fingerprints of the exact text
that was fenced, not a classification of the whole conversation.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass

MIN_FINGERPRINT = 12
MAX_HAYSTACK = 100_000

NETWORK_TOOLS = frozenset({"read_url", "web_search"})
SECRET_HINTS = (
    ".ssh",
    ".gnupg",
    "id_rsa",
    "serve.token",
    "policy.toml",
    ".env",
    "credentials",
    "api_key",
    "apikey",
    "secret",
    "password",
)


@dataclass(frozen=True)
class Mark:
    origin: str
    fingerprint: str


MARKS: ContextVar[tuple[Mark, ...]] = ContextVar("sk_taint_marks", default=())


def _fingerprint(text: object, *, minimum: int = MIN_FINGERPRINT) -> str:
    value = re.sub(r"\s+", " ", str(text if text is not None else "")).strip().lower()
    return value if len(value) >= minimum else ""


def reset() -> None:
    """Start a turn with no taint."""
    MARKS.set(())


def mark(text: str, origin: str) -> str:
    """Record that `text` came from `origin`; return it unchanged for fencing."""
    fingerprint = _fingerprint(text)
    if fingerprint:
        MARKS.set(MARKS.get() + (Mark(str(origin or "untrusted"), fingerprint),))
    return text


def origins_in(value: object) -> tuple[str, ...]:
    """Origins whose fenced text appears in `value` (or vice versa)."""
    hay = _fingerprint(value[:MAX_HAYSTACK] if isinstance(value, str) else value)
    if not hay:
        return ()
    return tuple(
        sorted(
            {
                item.origin
                for item in MARKS.get()
                if (item.fingerprint in hay) or (hay in item.fingerprint)
            }
        )
    )


def _strings(value: object, out: list[str]) -> None:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            _strings(key, out)
            _strings(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _strings(item, out)


def origins_in_args(args: dict) -> tuple[str, ...]:
    out: list[str] = []
    _strings(args, out)
    return tuple(sorted({origin for text in out for origin in origins_in(text)}))


def secret_adjacent(args: dict) -> bool:
    """A tool argument names something secret-adjacent, tainted or not."""
    out: list[str] = []
    _strings(args, out)
    hay = " ".join(out).lower()
    return any(hint in hay for hint in SECRET_HINTS)


def decide(name: str, args: dict) -> tuple[str, str, tuple[str, ...]]:
    """Return (decision, reason, origins); decision is allow/approve/deny.

    - no taint → allow, with no change to existing approval behaviour;
    - a secret-adjacent argument from an untrusted source → deny, even in
      auto-approve mode;
    - writes, shell, network fetches and anything already approval-gated →
      approve when an approver exists.
    """
    origins = origins_in_args(args)
    if not origins:
        return "allow", "", ()
    label = ", ".join(origins)
    if secret_adjacent(args):
        return (
            "deny",
            f"untrusted text from {label} names a secret-adjacent path or value; refusing to read or change it",
            origins,
        )
    from .tools.registry import approval_tools

    if name in approval_tools() or name in NETWORK_TOOLS:
        return (
            "approve",
            f"{name} uses text from untrusted {label}; an approval is required before it can act",
            origins,
        )
    return "allow", "", origins


def audit_target(target: str, decision: str, origins: tuple[str, ...]) -> str:
    """Embed the taint decision in the existing audit row's target field."""
    if decision == "allow" and not origins:
        return target
    label = ",".join(origins) or "none"
    return f"taint={decision} origin={label} {target}".strip()
