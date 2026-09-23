"""Clipboard copy without hard deps. Order: wl-copy -> pbcopy (macOS) -> xclip -> xsel -> OSC52.

Tool backends are reliable. OSC52 is a last resort: many terminals
(gnome-terminal/VTE included) silently ignore it, so callers must warn
when it is the only option. See backends_available().
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys

TOOL_BACKENDS = ("wl-copy", "pbcopy", "xclip", "xsel")


def backends_available() -> list[str]:
    """Clipboard helper binaries present on PATH."""
    return [b for b in TOOL_BACKENDS if shutil.which(b)]


def install_hint() -> str:
    return (
        "no clipboard helper found — `sudo apt install xclip` (Linux) or use pbcopy/OSC52 on macOS"
    )


def osc52_sequence(text: str) -> str:
    b64 = base64.b64encode(text.encode("utf-8", errors="replace")).decode()
    return f"\x1b]52;c;{b64}\x07"


def _serve(argv: list[str], data: bytes) -> bool:
    """Start a clipboard owner without waiting for it.

    X11 owners must stay alive to serve pastes, so waiting (like
    subprocess.run does) hangs forever on xclip, and -loops 1 exits
    without serving at all. Spawn detached; a fast non-zero exit means
    real failure (no X, bad display). DEVNULL avoids pipe inheritance
    keeping any waiter alive.
    """
    try:
        p = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return False
    except Exception:
        return False
    try:
        assert p.stdin is not None
        p.stdin.write(data)
        p.stdin.close()
    except BrokenPipeError:
        pass
    except Exception:
        return False
    try:
        rc = p.wait(timeout=0.5)
        return rc == 0
    except subprocess.TimeoutExpired:
        return True  # still running = serving pastes


def copy_text(text: str) -> str:
    """Copy text to clipboard. Returns method used: wl-copy|xclip|xsel|osc52.

    NOTE: "osc52" means "sent, unconfirmed" — the terminal may ignore it.
    Callers should append install_hint() when no tool backend exists.
    """
    if not text:
        raise ValueError("nothing to copy")
    data = text.encode("utf-8", errors="replace")
    if shutil.which("wl-copy"):
        if _serve(["wl-copy"], data):
            return "wl-copy"
    if shutil.which("pbcopy"):
        if _serve(["pbcopy"], data):
            return "pbcopy"
    if shutil.which("xclip"):
        if _serve(["xclip", "-selection", "clipboard"], data):
            return "xclip"
    if shutil.which("xsel"):
        if _serve(["xsel", "--clipboard", "--input"], data):
            return "xsel"
    # last resort: terminal handles it
    sys.stdout.write(osc52_sequence(text))
    sys.stdout.flush()
    return "osc52"
