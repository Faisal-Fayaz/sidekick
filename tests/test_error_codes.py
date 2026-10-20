"""Failure classification for `sk run --json` (the machine-readable contract).

The bug this locks down: a turn that died mid-flight returned its error text
as if it were the answer, so the JSON envelope said `ok: true` for a turn that
never produced one. A harness that trusted `ok` scored a rate-limited run as a
completed turn -- the exact failure the Terminal-Bench adapter was built to
detect.
"""

from __future__ import annotations

import pytest

from sk.errors import (
    AUTH,
    BAD_REQUEST,
    ERROR_CODES,
    NETWORK,
    RATE_LIMIT,
    SERVER_ERROR,
    TIMEOUT,
    TURN_FAILED,
    TurnFailure,
    classify_error,
    describe_error,
)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Error code: 429 - {'error': {'message': 'Rate limit exceeded'}}", RATE_LIMIT),
        ("Error code: 401 - invalid api key", AUTH),
        ("Error code: 403 - forbidden", AUTH),
        ("Error code: 400 - bad request", BAD_REQUEST),
        ("Error code: 404 - not found", BAD_REQUEST),
        ("Error code: 500 - internal server error", SERVER_ERROR),
        ("Error code: 503 - service unavailable", SERVER_ERROR),
        ("Error code: 529 - overloaded", SERVER_ERROR),
        ("Read timed out. (read timeout=30)", TIMEOUT),
        ("All connection attempts failed", NETWORK),
        ("something entirely unclassifiable", TURN_FAILED),
    ],
)
def test_status_codes_map_to_stable_codes(message, expected):
    assert classify_error(RuntimeError(message)) == expected


def test_status_code_attribute_wins_over_the_message():
    class _Exc(Exception):
        status_code = 429

    assert classify_error(_Exc("upstream said 503")) == RATE_LIMIT


def test_primary_status_beats_a_quoted_earlier_error():
    """OpenRouter's 400 body quotes the 429 that preceded it in `previous_errors`.

    The 400 is the error the provider actually returned, so that is what the
    envelope should say -- otherwise every throttled run is reported as a
    malformed request and nobody can tell an infra limit from a real bug.
    """
    message = (
        "Error code: 400 - {'error': {'message': 'Provider returned error', 'code': 400, "
        "'previous_errors': [{'code': 429, 'message': 'Rate limit exceeded: free-models-per-min.'}]}}"
    )
    assert classify_error(RuntimeError(message)) == BAD_REQUEST


def test_a_url_in_the_message_is_not_read_as_a_status_code():
    """A loose pattern would pull the host and port out of a URL."""
    message = "connection refused for http://127.0.0.1:8080/v1/chat"
    assert classify_error(RuntimeError(message)) == NETWORK


def test_keyword_fallback_when_there_is_no_code():
    assert classify_error(RuntimeError("Rate limit exceeded, slow down")) == RATE_LIMIT
    assert classify_error(RuntimeError("Unauthorized: check your api key")) == AUTH
    assert classify_error(RuntimeError("Request timed out")) == TIMEOUT


def test_classify_never_raises():
    class _Hostile(Exception):
        def __str__(self):
            raise RuntimeError("no string for you")

    assert classify_error(_Hostile()) in ERROR_CODES


def test_describe_error_returns_a_short_message_and_a_code():
    message, code = describe_error(RuntimeError("Error code: 429 - slow down"))
    assert "429" in message
    assert code == RATE_LIMIT


def test_describe_error_never_raises():
    class _Hostile(Exception):
        def __str__(self):
            raise RuntimeError("no string for you")

    message, code = describe_error(_Hostile())
    assert message
    assert code in ERROR_CODES


def test_turn_failure_is_still_text():
    """Every existing consumer treats the answer as a string; the failure has
    to stay one, or the TUI, the transcript and the jobs registry all break."""
    failure = TurnFailure("Turn failed partway: boom", error="boom", code=RATE_LIMIT)
    assert isinstance(failure, str)
    assert failure.startswith("Turn failed partway")
    assert failure.error == "boom"
    assert failure.code == RATE_LIMIT


def test_turn_failure_defaults_to_a_known_code():
    assert TurnFailure("boom").code == TURN_FAILED
    assert TurnFailure("boom", code="not-a-code").code == TURN_FAILED
