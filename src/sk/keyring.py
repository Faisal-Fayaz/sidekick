"""OS keyring backend for API keys. stdlib subprocess only, never raises.

Linux: secret-tool (libsecret). macOS: security CLI. Secrets travel via
stdin, never argv (visible in ps). Absent backend -> file fallback, handled
by callers. All functions return ""/False on any failure.
"""

from __future__ import annotations

import shutil
import subprocess

SERVICE = "sidekick-agent"


def backend() -> str:
    """'secret-tool' | 'security' | '' (none available)."""
    if shutil.which("secret-tool"):
        return "secret-tool"
    if shutil.which("security"):
        return "security"
    return ""


def get_key(provider: str) -> str:
    """Fetch a key from the OS keyring. "" when absent or unavailable."""
    tool = backend()
    if not tool or not (provider or "").strip():
        return ""
    try:
        if tool == "secret-tool":
            r = subprocess.run(
                ["secret-tool", "lookup", "service", SERVICE, "account", provider.strip()],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        r = subprocess.run(
            ["security", "find-generic-password", "-a", provider.strip(), "-s", SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def set_key(provider: str, secret: str) -> bool:
    """Store a key in the OS keyring. False when unavailable or failed."""
    tool = backend()
    if not tool or not (provider or "").strip() or not (secret or "").strip():
        return False
    secret = secret.strip()
    try:
        if tool == "secret-tool":
            r = subprocess.run(
                [
                    "secret-tool",
                    "store",
                    "--label",
                    f"{SERVICE} {provider.strip()}",
                    "service",
                    SERVICE,
                    "account",
                    provider.strip(),
                ],
                input=secret,
                capture_output=True,
                text=True,
                timeout=15,
            )
            return r.returncode == 0
        delete_key(provider)  # security has no overwrite; delete-then-add
        r = subprocess.run(
            ["security", "add-generic-password", "-a", provider.strip(), "-s", SERVICE, "-w"],
            input=secret,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def delete_key(provider: str) -> bool:
    """Remove a key from the OS keyring. True when gone (or never there)."""
    tool = backend()
    if not tool or not (provider or "").strip():
        return False
    try:
        if tool == "secret-tool":
            r = subprocess.run(
                ["secret-tool", "clear", "service", SERVICE, "account", provider.strip()],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return r.returncode == 0
        r = subprocess.run(
            ["security", "delete-generic-password", "-a", provider.strip(), "-s", SERVICE],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # security exits 44 when the item never existed: still "gone".
        return r.returncode in (0, 44)
    except Exception:
        return False
