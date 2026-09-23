"""Self-update: PyPI version check + installer-aware upgrade. No network except PyPI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PACKAGE = "sidekick-agent"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"


def pypi_latest_version(timeout: int = 15) -> str:
    """Latest released version from PyPI. Raises RuntimeError offline/on error."""
    import httpx

    try:
        r = httpx.get(PYPI_URL, timeout=timeout)
        r.raise_for_status()
        version = str(r.json().get("info", {}).get("version", "")).strip()
    except Exception as e:
        raise RuntimeError(f"cannot reach PyPI: {e}") from e
    if not version:
        raise RuntimeError("PyPI returned no version")
    return version


def parse_version(s: str) -> tuple[int, ...]:
    """Numeric prefix as a comparable tuple. '0.10.0' > '0.9.0' (unlike strings)."""
    import re

    parts = re.findall(r"\d+", s or "")
    if not parts:
        raise ValueError(f"unparseable version: {s!r}")
    return tuple(int(p) for p in parts[:4])


def is_newer(latest: str, current: str) -> bool:
    """True if latest > current. Unparseable input falls back to inequality."""
    try:
        return parse_version(latest) > parse_version(current)
    except ValueError:
        return latest.strip() != current.strip()


def detect_installer(exe: str = "") -> str:
    """How this copy was installed: uv | pipx | pip | dev (editable checkout)."""
    exe_path = (exe or sys.executable or "").replace("\\", "/")
    try:
        import sk as _sk

        pkg_dir = Path(getattr(_sk, "__file__", "") or "").resolve().parent
        for parent in [pkg_dir, *list(pkg_dir.parents)[:4]]:
            is_checkout = (parent / ".git").is_dir() or (
                (parent / "pyproject.toml").is_file() and (parent / "src" / "sk").is_dir()
            )
            if is_checkout:
                return "dev"
    except Exception:
        pass
    low = exe_path.lower()
    if "/uv/tools/" in low or "/.local/share/uv/" in low:
        return "uv"
    if "/pipx/" in low or "pipx/venvs" in low:
        return "pipx"
    return "pip"


def upgrade_argv(installer: str) -> list[str]:
    """Upgrade command for an installer. Raises for dev/unknown."""
    if installer == "uv":
        return ["uv", "tool", "install", "--force", PACKAGE]
    if installer == "pipx":
        return ["pipx", "upgrade", PACKAGE]
    if installer == "pip":
        return [sys.executable or "python3", "-m", "pip", "install", "-U", PACKAGE]
    raise RuntimeError(
        "editable dev install — upgrade with git pull + reinstall instead of sk upgrade."
    )


def upgrade_package(installer: str, timeout: int = 180) -> tuple[bool, str]:
    """Run the installer upgrade. Returns (ok, output tail). Never raises."""
    try:
        argv = upgrade_argv(installer)
    except RuntimeError as e:
        return (False, str(e))
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-5:]
        out = "\n".join(tail) or "(no output)"
        return (r.returncode == 0, out)
    except Exception as e:
        return (False, str(e)[:300])
