"""Evidence-backed code review: target diff -> findings -> report.

The package is intentionally split so each layer is testable offline:

- ``diff``     target resolution + unified-diff parsing (no model)
- ``schema``   finding/assessment models, evidence validation, serialization
- ``checks``   deterministic Python checks (no model)
- ``intent``   stated-intent resolution for the worth-it layer
- ``signals``  deterministic worth-it signals (no model)
- ``model``    one-shot structured model call (OpenAI-compatible + Anthropic)
- ``providers`` GitHub/Bitbucket PR metadata, diff, and comment posting
- ``engine``   orchestration: diff -> checks -> model -> validate -> report

Nothing here writes source files or executes repository code. PR posting is the
only external side effect and is explicit (``sk review --pr``).
"""

from __future__ import annotations

__all__ = ["review", "ReviewOptions", "ReviewError"]

from .engine import ReviewError, ReviewOptions, review  # noqa: E402
