"""Clipboard copy without hard deps. Order: wl-copy -> xclip -> xsel -> OSC52.

OSC52 prints an escape to the terminal, which works over SSH/tmux in most
modern terminals (kitty, wezterm, xterm, recent VTE). No mouse selection needed.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys


def osc52_sequence(text: str) -> str:
    b64 = base64.b64encode(text.encode("utf-8", errors="replace")).decode()
    return f"\x1b]52;c;{b64}\x07"


def copy_text(text: str) -> str:
    """Copy text to clipboard. Returns method used: wl-copy|xclip|xsel|osc52.

    Raises RuntimeError if nothing works (caller should show install hint).
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
