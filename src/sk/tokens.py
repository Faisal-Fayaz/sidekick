"""One token estimator (#313).

There were two, and they disagreed on every non-empty string:

    store._est_tokens      len(text) // 4            floor
    agent.estimate_tokens  (len(text) + 3) // 4      ceil, floor 1

Both docstrings claimed to match the other. So `sk stats` and the context
compaction path counted the same text differently, and the accounting side
under-reported by up to one token per stored string.

One function, one arithmetic rule — round up, because a partial token is still a
token. The only genuine semantic difference between the two former callers is
whether an empty string counts as zero or one, so that stays an explicit
argument rather than a second definition:

- accounting (`floor=0`): an empty message consumed nothing.
- context budgeting (`floor=1`): every entry in a prompt costs something to
  serialise, so an empty one is not free. Left exactly as it was, deliberately
  — context-limit arithmetic is #308's subject and not this PR's to move.

Still a ~4 chars/token approximation. Exact tokenizers are model-specific and
would be a new dependency; the codebase has always budgeted by characters. What
this fixes is the two definitions disagreeing, not the approximation itself.
"""

from __future__ import annotations

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str, *, floor: int = 0) -> int:
    """Approximate token count for `text`, rounded up. Never raises.

    `floor` is the minimum to return, so callers that need "an empty string still
    costs something" say so instead of defining their own estimator.
    """
    try:
        n = len(text or "")
    except Exception:
        return floor
    return max(floor, (n + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)
