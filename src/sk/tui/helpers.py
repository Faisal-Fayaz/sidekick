"""TUI log/render/history helpers (split from sk/tui.py, pure move)."""

from __future__ import annotations

import time

from rich.text import Text
from textual.widgets import RichLog

ROLE_STYLES = {
    "you": "bold green",
    "sidekick": "bold cyan",
    "tool": "dim",
    "sys": "dim",
    "warn": "bold yellow",
    "error": "bold red",
}


def _w(log: RichLog, s: str, markup: bool = False) -> None:
    """Write to log. markup=True only for our own chrome (no user/model brackets)."""
    log.write(Text.from_markup(s) if markup else Text(s))


def log_error(where: str, exc: BaseException) -> None:
    """Persist TUI errors where the user can't copy them. Best effort."""
    import traceback

    from sk.config import CONFIG_DIR

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_DIR / "tui-errors.log", "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {where}: {exc!r}\n")
            f.write(traceback.format_exc()[-2000:] + "\n")
    except Exception:
        pass


def _line(when: str, role: str, body: str) -> Text:
    """Role-colored chat line: dim timestamp, colored `role>`, neutral body.

    Bodies stay neutral on purpose — brackets and code copy cleanly and the
    role color alone carries who-is-who.
    """
    t = Text()
    t.append(f"[{when}] ", style="dim")
    if role:
        t.append(f"{role}> ", style=ROLE_STYLES.get(role, ""))
    t.append(body)
    return t


def _role(log: RichLog, role: str, body: str) -> None:
    log.write(_line(_now(), role, body))


def _rule(log: RichLog) -> None:
    t = Text("─" * 40, style="dim")
    log.write(t)


def _now() -> str:
    return time.strftime("%H:%M")


# Words that approve a pending write. Keep in sync with the prompt line.
# Multi-word entries match when the whole line starts with them ("go ahead
# and write it" counts; "yeah but not there" does not — strict startswith).
AFFIRMATIVE_EXACT = (
    "y",
    "yes",
    "yup",
    "ok",
    "okay",
    "sure",
    "approve",
    "--yes",
    "-y",
    "yeah",
    "yep",
    "yepp",
    "aye",
)
AFFIRMATIVE_PREFIX = ("go ahead", "do it", "yes please", "please do")


def is_affirmative(text: str) -> bool:
    t = (text or "").strip().lower()
    if t in AFFIRMATIVE_EXACT:
        return True
    return any(t.startswith(p) for p in AFFIRMATIVE_PREFIX)


def _load_history() -> list[str]:
    import json

    from sk.config import CONFIG_DIR

    try:
        items = json.loads((CONFIG_DIR / "input_history").read_text())
        return [str(x) for x in items if str(x).strip()][:100]
    except Exception:
        return []


def _save_history(items: list[str]) -> None:
    import json

    from sk.config import CONFIG_DIR

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        (CONFIG_DIR / "input_history").write_text(json.dumps(items[-100:]))
    except Exception:
        pass
