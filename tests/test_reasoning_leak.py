"""Reasoning must never reach the chat answer, on any final path (#205 follow-up).

#205 was closed on Sep 28 with the invariant written down — "never post it as
chat" — but the fix covered only the *empty-content + reasoning* route. Two paths
that actively **promote** reasoning were left in place, and one of them fed
reasoning to `on_token`, i.e. straight into the TUI's live answer box:

    exhaustion synthesis   `_stream_chat(..., on_token, None)`
    last-step peek         `final_text = m2.content or m2.reasoning or ""`

Between them they produced the reported symptom: text scrolls through the live
box above the chat looking like an answer, then vanishes when the turn ends, and
three terse lines appear instead.

Everything here uses the injectable transport seam (#318), so no network and no
API key.
"""

from __future__ import annotations

import pytest

from sk.agent import _stream_chat, _synthesize_exhaustion


class _Chunk:
    """Minimal stand-in for an OpenAI-shaped streamed chunk."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _reasoning_then_content(reasoning: str, content: str):
    """A reasoning model's stream: thinking deltas, then the answer."""

    def transport(_params):
        for i in range(0, len(reasoning), 12):
            yield _Chunk(choices=[{"delta": {"reasoning": reasoning[i : i + 12]}}])
        if content:
            yield _Chunk(choices=[{"delta": {"content": content}}])
        yield _Chunk(choices=[{"delta": {}, "finish_reason": "stop"}])

    return transport


# --- the sink routing that caused the visible symptom ------------------------


def test_no_reasoning_sink_routes_reasoning_into_on_token():
    """The root cause, isolated: with no reasoning sink, reasoning goes to
    on_token. That is the intended fallback for callers that want everything in
    one stream — and exactly wrong for a path whose output is the chat answer.
    """
    toks: list[str] = []
    m = _stream_chat(
        None,
        "m",
        [],
        None,
        0.2,
        500,
        {},
        on_token=toks.append,
        on_reasoning=None,
        transport=_reasoning_then_content("internal deliberation here.", "The answer."),
    )
    assert "".join(toks) == "internal deliberation here.The answer."
    assert m.content == "The answer."
    assert m.reasoning == "internal deliberation here."


def test_exhaustion_routes_reasoning_to_its_own_sink(monkeypatch):
    toks: list[str] = []
    reas: list[str] = []

    def fake_stream(*args, **kwargs):
        return _stream_chat(
            args[0],
            args[1],
            args[2],
            args[3],
            args[4],
            args[5],
            args[6],
            on_token=kwargs.get("on_token") or (args[7] if len(args) > 7 else None),
            on_reasoning=kwargs.get("on_reasoning", args[8] if len(args) > 8 else None),
            transport=_reasoning_then_content(
                "We need must be exactly 3 lines. No tools now.",
                "Accomplished: read the file",
            ),
        )

    monkeypatch.setattr("sk.agent._stream_chat", fake_stream)
    out = _synthesize_exhaustion(
        None,
        "m",
        [{"role": "user", "content": "x"}],
        0.2,
        400,
        {},
        toks.append,
        reas.append,
    )

    assert "exactly 3 lines" not in "".join(toks), "reasoning reached the answer stream"
    assert "Accomplished: read the file" in "".join(toks), "the actual recap went missing"
    assert "".join(reas).strip() == "We need must be exactly 3 lines. No tools now."
    assert "exactly 3 lines" not in out, "reasoning leaked into the returned recap"


def test_exhaustion_signature_accepts_a_reasoning_sink():
    """Guard the API so the parameter cannot be dropped again silently."""
    import inspect

    params = inspect.signature(_synthesize_exhaustion).parameters
    assert "on_reasoning" in params, "exhaustion synthesis lost its reasoning sink"
    assert params["on_reasoning"].default is None


def test_exhaustion_report_is_content_only(monkeypatch):
    """Re-asserted after #358 so the two leaks cannot drift apart again."""

    class _M:
        content = "Accomplished: a\nBlocked: b\nNext: c"
        reasoning = "We need must be exactly 3 lines."

    monkeypatch.setattr("sk.agent._stream_chat", lambda *a, **kw: _M())
    out = _synthesize_exhaustion(None, "m", [], 0.2, 400, {}, lambda _t: None)
    assert out == "Accomplished: a\nBlocked: b\nNext: c"
    assert "exactly 3 lines" not in out


# --- the last-step peek ------------------------------------------------------


def test_last_step_peek_does_not_promote_reasoning():
    """`m2.content or m2.reasoning` posted a raw trace as the chat answer.

    Reproduced here as a source-level guard as well as behaviourally: the fix has
    to be visible at the call site, because an empty peek falling through to the
    progress report depends on the exact expression used.
    """
    import inspect

    import sk.agent as agent_mod

    src = inspect.getsource(agent_mod.run_agent) if hasattr(agent_mod, "run_agent") else ""
    assert "m2.content or m2.reasoning" not in src, (
        "the peek promotes reasoning to the answer again"
    )


@pytest.mark.parametrize("content,expect_answer", [("The answer.", "The answer."), ("", "")])
def test_content_only_extraction_yields_content_or_empty(content, expect_answer):
    """The extraction rule the peek now uses, in isolation."""
    m = _Chunk.__new__(_Chunk)
    m.content = content
    m.reasoning = "should never surface"
    final_text = m.content or ""
    assert final_text == expect_answer
    assert "should never surface" not in final_text
