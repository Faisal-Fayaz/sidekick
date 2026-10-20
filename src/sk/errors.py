"""Machine-readable failure classification for an agent turn.

`sk run --json` is a machine interface: a caller has to tell a throttled
provider from a rejected request from a network blip without scraping prose.
The turn used to launder every failure into the answer text ("Turn failed
partway: Error code: 429 - ..."), so a caller saw `ok: true` for a turn that
never produced an answer, and a caller who wanted the truth had to regex the
string.

Two pieces live here:

- `TurnFailure`: a `str` subclass that carries a failed turn as its own text.
  Every existing consumer (TUI, chat, background jobs, export) keeps working
  on the text unchanged, because it *is* the text, while a machine consumer can
  tell a failed turn from a successful one.
- `classify_error`: maps an exception to a stable code.

The code set is part of the JSON contract, so it is deliberately small and
only grows on purpose.
"""

from __future__ import annotations

import re

# The stable code set. Callers may switch on these strings.
RATE_LIMIT = "rate_limit"
AUTH = "auth"
SERVER_ERROR = "server_error"
TIMEOUT = "timeout"
NETWORK = "network"
BAD_REQUEST = "bad_request"
TURN_FAILED = "turn_failed"
JOB_FAILED = "job_failed"

ERROR_CODES = (
    RATE_LIMIT,
    AUTH,
    SERVER_ERROR,
    TIMEOUT,
    NETWORK,
    BAD_REQUEST,
    TURN_FAILED,
    JOB_FAILED,
)

# Primary status code -> code. Anything in 5xx is a server error; 408 is a
# timeout; the rest of the 4xx are the caller's problem, not ours.
_STATUS_CODES = {
    400: BAD_REQUEST,
    401: AUTH,
    403: AUTH,
    404: BAD_REQUEST,
    408: TIMEOUT,
    422: BAD_REQUEST,
    429: RATE_LIMIT,
}

# Only consulted when no status code can be found. Order matters: the first
# match wins, so the specific ones come before the generic ones.
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (RATE_LIMIT, ("rate limit", "too many requests", "resource_exhausted", "quota exceeded")),
    (AUTH, ("unauthorized", "invalid api key", "authentication", "api key", "forbidden")),
    (TIMEOUT, ("timed out", "timeout")),
    (NETWORK, ("connection", "unreachable", "network", "dns", "name resolution", "ssl")),
    (SERVER_ERROR, ("internal server error", "bad gateway", "service unavailable", "overloaded")),
    (BAD_REQUEST, ("bad request", "invalid request", "validation error")),
)

# "Error code: 429 - {...}", "status code: 500", "HTTP/1.1 503", "status=429".
# Deliberately narrow: a loose "http ... 3 digits" pattern would read the host
# and port out of a URL and call a network error a bad request.
_STATUS_RES = (
    re.compile(r"error code[:=\s]*(\d{3})\b", re.IGNORECASE),
    re.compile(r"status code[:=\s]*(\d{3})\b", re.IGNORECASE),
    re.compile(r"http/\d(?:\.\d)?\s+(\d{3})\b", re.IGNORECASE),
    re.compile(r"\bhttp\s+(\d{3})\b", re.IGNORECASE),
    re.compile(r"\bstatus[:=]\s*(\d{3})\b", re.IGNORECASE),
)


class TurnFailure(str):
    """A turn that ended in an error, carried as its own text.

    Subclasses `str` so every existing consumer keeps working on the text
    unchanged, while the CLI can report the outcome of the turn instead of
    just that the command ran. `error` is the cause and `code` is one of
    `ERROR_CODES`.
    The failure carries its cause and a classification, so the CLI can report
    the outcome of the turn instead of just that the command ran.
    """

    error: str
    code: str

    def __new__(cls, text: str, *, error: str = "", code: str = TURN_FAILED) -> TurnFailure:
        self = super().__new__(cls, text)
        self.error = error or str(text)
        self.code = code if code in ERROR_CODES else TURN_FAILED
        return self


def _status_code(exc: BaseException) -> int | None:
    """The primary status code of an API failure, if the exception names one."""
    raw = getattr(exc, "status_code", None)
    if isinstance(raw, bool):
        raw = None
    if isinstance(raw, int) and 100 <= raw < 600:
        return raw
    try:
        text = str(exc)
    except Exception:
        return None
    for pattern in _STATUS_RES:
        m = pattern.search(text)
        if not m:
            continue
        try:
            code = int(m.group(1))
        except ValueError:
            continue
        if 100 <= code < 600:
            return code
    return None


def classify_error(exc: BaseException | str) -> str:
    """Map an exception (or an error message) to one of `ERROR_CODES`.

    The primary status code wins over keyword matching: OpenRouter's 400 body
    can quote an earlier 429 in `previous_errors`, and the 400 is the error the
    provider actually returned. Never raises.
    """
    try:
        if isinstance(exc, BaseException):
            status = _status_code(exc)
        else:
            status = None
            for pattern in _STATUS_RES:
                m = pattern.search(exc)
                if not m:
                    continue
                try:
                    status = int(m.group(1))
                except ValueError:
                    continue
                break
        if status is not None:
            if status in _STATUS_CODES:
                return _STATUS_CODES[status]
            if 500 <= status < 600:
                return SERVER_ERROR
            return BAD_REQUEST
        try:
            text = (str(exc) or "").lower()
        except Exception:
            text = ""
        for code, needles in _KEYWORDS:
            if any(n in text for n in needles):
                return code
        return TURN_FAILED
    except Exception:
        return TURN_FAILED


def describe_error(exc: BaseException) -> tuple[str, str]:
    """Return `(message, code)` for an exception. Never raises.

    The message is short enough to sit in the JSON envelope's `error` field
    without the caller having to truncate it.
    """
    try:
        message = str(exc).strip()[:300] or type(exc).__name__
    except Exception:
        message = type(exc).__name__
    try:
        code = classify_error(exc)
    except Exception:
        code = TURN_FAILED
    return message, code
