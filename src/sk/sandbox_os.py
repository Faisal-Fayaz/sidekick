"""OS-level enforcement for the sandbox (Phase 2 of #440).

The declarative policy (Phase 1) is enforced by reading the command text. That
is a floor, not a ceiling: a program that runs can do things the parser never
saw. This module is the other half — the boundary the kernel enforces.

Two shapes, one rule: **never fail closed on a missing optional dependency.**
bubblewrap or Seatbelt are used when they are present; when they are not, the
policy stays enforced where it can be (the tool layer) and `sk doctor` says the
OS enforcement is advisory.

What the OS layer enforces here is the **write** boundary: the declared roots
are writable, everything else is read-only. The read boundary stays with the
policy, because hiding the filesystem from a process breaks it in ways that are
hard to explain — a python that cannot see ``/usr``, a git that cannot see its
config — and buys little that the policy does not already do.

Network: when the policy allows no hosts, the process gets no network at all
(``--unshare-net``). Selective allow-listing would need a relay, which is not
implemented; when hosts are allowed the network is left alone and the egress
policy governs it.

Per-platform support follows the Claude Code precedent: WSL2 runs the Linux
path, native Windows is not supported.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Enforcement:
    """What this machine can enforce."""

    available: bool = False
    backend: str = ""  # "bwrap" | "seatbelt"
    reason: str = ""

    def describe(self) -> str:
        if not self.available:
            return f"advisory ({self.reason})"
        return f"enforced ({self.backend})"


def _is_macos() -> bool:
    return sys.platform == "darwin" or os.uname().sysname == "Darwin"


def detect() -> Enforcement:
    """The OS enforcement available here. Never raises."""
    try:
        if shutil.which("bwrap"):
            return Enforcement(available=True, backend="bwrap", reason="bubblewrap")
        if _is_macos() and shutil.which("sandbox-exec"):
            return Enforcement(available=True, backend="seatbelt", reason="Seatbelt")
        return Enforcement(
            available=False,
            backend="",
            reason="neither bubblewrap nor Seatbelt is installed",
        )
    except Exception as e:  # noqa: BLE001 - detection must never break a shell call
        return Enforcement(available=False, backend="", reason=f"detection failed: {e}")


def _roots(write_roots: list[str] | tuple[str, ...], cwd: str) -> list[str]:
    """The paths to bind writable, with the working directory always included.

    The working directory has to be visible: a process that cannot see where it
    is fails in ways that look like a broken tool rather than a sandbox.
    """
    out: list[str] = []
    for raw in list(write_roots or []) + [cwd]:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            path = Path(os.path.expanduser(text)).resolve()
        except Exception:
            continue
        value = str(path)
        if value not in out:
            out.append(value)
    return out


def bwrap_command(
    cmd: str,
    *,
    write_roots: list[str] | tuple[str, ...] = (),
    cwd: str = "",
    allow_network: bool = False,
) -> str:
    """The command, wrapped in a bubblewrap sandbox.

    The filesystem is read-only except for the declared roots; ``/tmp`` is a
    fresh tmpfs so a command that needs scratch space still has some, without
    the host's ``/tmp`` being visible or writable.
    """
    args: list[str] = ["bwrap", "--ro-bind", "/", "/", "--tmpfs", "/tmp"]
    for root in _roots(write_roots, cwd):
        args.extend(["--bind", root, root])
    args.extend(
        [
            "--dev-bind",
            "/dev",
            "/dev",
            "--proc",
            "/proc",
            "--unshare-pid",
            "--die-with-parent",
            "--new-session",
        ]
    )
    if not allow_network:
        args.append("--unshare-net")
    args.extend(["bash", "-c", cmd])
    return " ".join(shlex.quote(part) for part in args)


def seatbelt_profile(
    *,
    write_roots: list[str] | tuple[str, ...] = (),
    cwd: str = "",
    allow_network: bool = False,
) -> str:
    """A Seatbelt profile: everything is denied, then what is needed is allowed.

    Seatbelt rules are evaluated in order and the last match wins, so the
    ``deny default`` comes first and the allowances after it.
    """
    lines = ["(version 1)", "(deny default)"]
    lines.append("(allow process*)")
    lines.append("(allow signal)")
    lines.append("(allow sysctl-read)")
    lines.append("(allow mach-lookup)")
    lines.append("(allow file-read*)")
    for root in _roots(write_roots, cwd):
        lines.append(f'(allow file-write* (subpath "{root}"))')
    if allow_network:
        lines.append("(allow network*)")
    return "\n".join(lines)


def seatbelt_command(
    cmd: str,
    *,
    write_roots: list[str] | tuple[str, ...] = (),
    cwd: str = "",
    allow_network: bool = False,
) -> str:
    """The command, wrapped in a Seatbelt sandbox."""
    profile = seatbelt_profile(write_roots=write_roots, cwd=cwd, allow_network=allow_network)
    return " ".join(
        shlex.quote(part) for part in ["sandbox-exec", "-p", profile, "bash", "-c", cmd]
    )


def wrap_command(
    cmd: str,
    *,
    write_roots: list[str] | tuple[str, ...] = (),
    cwd: str = "",
    allow_network: bool = False,
) -> tuple[str, str]:
    """Wrap a command for the strongest enforcement this machine has.

    Returns (command, note). The note is empty when nothing was wrapped — the
    command then runs as it always has, and the caller says so rather than
    pretending the sandbox is on.
    """
    enforcement = detect()
    if not enforcement.available:
        return cmd, ""
    try:
        if enforcement.backend == "bwrap":
            wrapped = bwrap_command(
                cmd, write_roots=write_roots, cwd=cwd, allow_network=allow_network
            )
        else:
            wrapped = seatbelt_command(
                cmd, write_roots=write_roots, cwd=cwd, allow_network=allow_network
            )
    except Exception:
        return cmd, ""
    return wrapped, enforcement.backend
