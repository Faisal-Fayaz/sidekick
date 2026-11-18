"""Opt-in, redacted crash reporting (#507).

**Off by default.** Nothing is written and no request is made unless the user
explicitly sets `SIDEKICK_TELEMETRY=1` or `telemetry = true` in the *global*
config. A project file cannot enable it. The default sink is a local JSONL file
under `~/.sidekick`; a remote endpoint is used only when the operator names one.

The payload is deliberately tiny: exception type, a redacted message, version,
Python version, OS and surface. Never prompts, file contents, paths, arguments,
environment values, stack locals or tracebacks.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import urllib.request
from pathlib import Path

SCHEMA = 1
TELEMETRY_FILE = "telemetry.jsonl"
MAX_MESSAGE = 500

_REDACTIONS = (
    (re.compile(r"sk-or-[A-Za-z0-9_-]+"), "<redacted-key>"),
    (re.compile(r"Bearer\s+\S+", re.IGNORECASE), "Bearer <redacted>"),
    (
        re.compile(r"(api[_-]?key|token|secret|password)\s*[=:]\s*\S+", re.IGNORECASE),
        r"\1=<redacted>",
    ),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<email>"),
    (re.compile(r"https?://\S+", re.IGNORECASE), "<url>"),
    (re.compile(r"(?<!\w)/[\w./~+-]{3,}"), "<path>"),
)


def _global_values() -> dict:
    from .config import CONFIG_PATH

    try:
        import tomllib

        with open(CONFIG_PATH, "rb") as handle:
            data = tomllib.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def enabled() -> bool:
    """True only with an explicit opt-in from the environment or global config."""
    raw = os.getenv("SIDEKICK_TELEMETRY", "").strip().lower()
    if raw:
        return raw in ("1", "true", "yes", "on")
    value = _global_values().get("telemetry", False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def endpoint() -> str:
    """An operator-named remote collector, or "" for the local sink only."""
    return (
        os.getenv("SIDEKICK_TELEMETRY_ENDPOINT", "").strip()
        or str(_global_values().get("telemetry_endpoint", "") or "").strip()
    )


def redact(text: str) -> str:
    out = str(text or "")
    for pattern, replacement in _REDACTIONS:
        out = pattern.sub(replacement, out)
    return out[:MAX_MESSAGE]


def payload(exc: BaseException, *, surface: str = "cli") -> dict:
    """A minimal, non-identifying report. Never includes a traceback."""
    import sk

    return {
        "schema": SCHEMA,
        "type": type(exc).__name__,
        "message": redact(str(exc)),
        "version": sk.__version__,
        "python": platform.python_version(),
        "os": platform.system(),
        "surface": str(surface or "cli")[:40],
    }


def report(exc: BaseException, *, surface: str = "cli") -> bool:
    """Write a redacted line locally and, if configured, to a remote collector.

    Best-effort: never raises and never changes the user-visible error or exit
    code. Returns False when reporting was disabled or nothing could be written.
    """
    if not enabled():
        return False
    line = json.dumps(payload(exc, surface=surface), sort_keys=True)
    from .config import CONFIG_DIR
    from .fsperm import PRIVATE_FILE, ensure_private_dir, tighten

    written = False
    try:
        ensure_private_dir(Path(CONFIG_DIR))
        path = Path(CONFIG_DIR) / TELEMETRY_FILE
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        tighten(path, want=PRIVATE_FILE)
        written = True
    except Exception:
        pass
    target = endpoint()
    if target:
        try:
            request = urllib.request.Request(
                target,
                data=line.encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=3):
                pass
        except Exception:
            pass
    return written


def install_excepthook() -> None:
    """Chain a crash reporter into the process excepthook, once, as a no-op when off.

    The hook delegates to the previous one in all cases, so it never swallows
    or reformats the original traceback.
    """
    if getattr(sys, "_sk_telemetry_hook", False):
        return
    previous = sys.excepthook

    def hook(kind, exc, tb):
        try:
            if enabled():
                report(exc, surface="cli")
        except Exception:
            pass
        previous(kind, exc, tb)

    sys.excepthook = hook
    sys._sk_telemetry_hook = True  # type: ignore[attr-defined]
