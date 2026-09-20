"""Clipboard copy without hard deps. Order: wl-copy -> xclip -> xsel -> OSC52.

Tool backends are reliable. OSC52 is a last resort: many terminals
(gnome-terminal/VTE included) silently ignore it, so callers must warn
when it is the only option. See backends_available().
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys

TOOL_BACKENDS = ("wl-copy", "xclip", "xsel")


def backends_available() -> list[str]:
    """Clipboard helper binaries present on PATH."""
    return [b for b in TOOL_BACKENDS if shutil.which(b)]


def install_hint() -> str:
    return "no clipboard helper found — `sudo apt install xclip`, then retry"


def osc52_sequence(text: str) -> str:
    b64 = base64.b64encode(text.encode("utf-8", errors="replace")).decode()
    return f"\x1b]52;c;{b64}\x07"


def copy_text(text: str) -> str:
    """Copy text to clipboard. Returns method used: wl-copy|xclip|xsel|osc52.

    NOTE: "osc52" means "sent, unconfirmed" — the terminal may ignore it.
    Callers should append install_hint() when no tool backend exists.
    """
    if not text:
        raise ValueError("nothing to copy")
    for bin_name, args in (
        ("wl-copy", []),
        ("xclip", ["-selection", "clipboard"]),
        ("xsel", ["--clipboard", "--input"]),
    ):
        if shutil.which(bin_name):
            try:
                subprocess.run(
                    [bin_name, *args], input=text.encode("utf-8", errors="replace"),
                    timeout=5, check=True, capture_output=True,
                )
                return bin_name
            except Exception:
                pass  # try next backend
    # last resort: terminal handles it
    sys.stdout.write(osc52_sequence(text))
    sys.stdout.flush()
    return "osc52"
