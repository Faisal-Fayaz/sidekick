"""Project checks: trust-gated config, parsers, and a bounded runner.

A check is a shell command from ``.sidekick/checks.toml``. The file is
untrusted unless the operator opted in with ``--trust-project-checks`` or the
existing ``SIDEKICK_TRUST_REPO=1`` environment opt-in. Loading a command never
runs it: every command still passes the normal shell approval gate and lands in
the audit ledger.

The package is deliberately independent of the review engine. Review can call
:func:`sk.check.runner.run_checks` for ``--verify`` without creating a second
command path.
"""

from __future__ import annotations

from .config import CheckCommand, CheckPack, load_checks_pack
from .parsers import Diagnostic, parse_output
from .runner import CheckReport, CheckResult, run_checks

__all__ = [
    "CheckCommand",
    "CheckPack",
    "CheckReport",
    "CheckResult",
    "Diagnostic",
    "load_checks_pack",
    "parse_output",
    "run_checks",
]
