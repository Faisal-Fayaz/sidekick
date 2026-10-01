"""Stream accumulator coverage through the transport seam (#318).

`_stream_chat` is the most important function in the codebase and had **zero**
coverage: 112 statements, exercised only by monkeypatching the module attribute
with a stub. That is exactly why #304 (duplicate tool_call_id) and #312 (Anthropic
stream re-emission) survived — a test that replaces the function under test
cannot see its bugs.

The seam (`transport(params) -> Iterable[chunk]`) plus the pure chunk normaliser
`_delta_events` make every branch reachable with no network, no SSE fixture, and
no monkeypatching.
"""

import pytest

from sk.agent import DeltaEvent, _delta_events, _stream_chat, _synthetic_call_id


def chunk(delta, finish=None):
    """A dict-shaped chunk. Real providers send objects; both are supported."""
    return {"choices": [{"delta": delta, "finish_reason": finish}]}


def call(params):
    """Run the accumulator over a chunk list. No network.

    The list is copied per call so the same fixture can be replayed to assert
    stability across a retry.
    """
    chunks = list(params)

    def transport(_p):
        return iter(list(chunks))

    return _stream_chat(None, "m", [], None, 0.2, 100, {}, transport=transport)


# --- normalisation ----------------------------------------------------------


def test_delta_events_object_chunks():
    class Obj:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    o = Obj(choices=[Obj(delta=Obj(content="hi"), finish_reason="stop")])
    evs = _delta_events(o)
    assert any(e.text == "hi" for e in evs)
    assert any(e.finish == "stop" for e in evs)


def test_delta_events_dict_chunks():
    evs = _delta_events(chunk({"content": "hi"}, "stop"))
    assert DeltaEvent(text="hi") in evs
    assert DeltaEvent(finish="stop") in evs


def test_delta_events_rejects_garbage_without_raising():
    """A malformed chunk must never break the stream."""
    for bad in (None, {}, {"choices": []}, {"choices": [{}]}, object(), 123):
        assert _delta_events(bad) == []


def test_delta_events_is_pure():
    c = chunk({"content": "x"})
    assert _delta_events(c) == _delta_events(c)


# --- accumulation -----------------------------------------------------------


def test_text_and_reasoning_go_to_separate_sinks():
    """Reasoning must not leak into the visible answer stream (#263 lineage)."""
    texts: list[str] = []
    reas: list[str] = []
    _stream_chat(
        None,
        "m",
        [],
        None,
        0.2,
        100,
        {},
        on_token=texts.append,
        on_reasoning=reas.append,
        transport=lambda p: iter(
            [chunk({"reasoning": "r1"}), chunk({"content": "a"}), chunk({"reasoning": "r2"})]
        ),
    )
    assert texts == ["a"]
    assert reas == ["r1", "r2"]


def test_reasoning_falls_back_to_on_token_when_no_sink():
    """Legacy callers pass only on_token and must still see reasoning."""
    seen: list[str] = []
    _stream_chat(
        None,
        "m",
        [],
        None,
        0.2,
        100,
        {},
        on_token=seen.append,
        transport=lambda p: iter([chunk({"reasoning": "r1"}), chunk({"content": "a"})]),
    )
    assert seen == ["r1", "a"]


def test_tool_call_fragments_concatenate_by_index():
    """name and arguments arrive across chunks and must be joined, not replaced."""
    m = call(
        [
            chunk({"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read"}}]}),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"pa'}}]}),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'th":"x"}'}}]}),
            chunk({}, "tool_calls"),
        ]
    )
    assert len(m.tool_calls) == 1
    assert m.tool_calls[0].id == "c1"
    assert m.tool_calls[0].function.name == "read"
    assert m.tool_calls[0].function.arguments == '{"path":"x"}'
    assert m.finish == "tool_calls"


def test_two_parallel_tool_calls_are_kept_separate():
    m = call(
        [
            chunk({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "one"}}]}),
            chunk({"tool_calls": [{"index": 1, "id": "b", "function": {"name": "two"}}]}),
        ]
    )
    names = sorted(c.function.name for c in m.tool_calls)
    assert names == ["one", "two"]
    assert sorted(c.id for c in m.tool_calls) == ["a", "b"]


def test_tool_call_without_index_defaults_to_zero():
    m = call([chunk({"tool_calls": [{"id": "x", "function": {"name": "n"}}]})])
    assert m.tool_calls[0].function.name == "n"


def test_partial_tool_call_yields_no_call():
    """A tool_call with no name yet is not dispatched (#226 empty-args guard)."""
    m = call([chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]})])
    assert m.tool_calls is None


def test_empty_tool_call_list_is_none_not_empty():
    """Downstream distinguishes [] from None; both must stay None here."""
    assert call([chunk({"tool_calls": []})]).tool_calls is None


def test_id_less_tool_calls_get_unique_stable_ids():
    """#310: ids must be unique per request and stable across a retry."""
    c = [
        chunk({"tool_calls": [{"index": 0, "function": {"name": "read", "arguments": '{"p":1}'}}]})
    ]
    first = call(c).tool_calls[0].id
    again = call(c).tool_calls[0].id
    assert first == again, "not stable across retries"
    other = call(
        [
            chunk(
                {"tool_calls": [{"index": 0, "function": {"name": "read", "arguments": '{"p":2}'}}]}
            )
        ]
    )
    assert other.tool_calls[0].id != first, "not unique for different calls"


def test_two_turns_without_ids_do_not_collide():
    t1 = call(
        [
            chunk(
                {"tool_calls": [{"index": 0, "function": {"name": "read", "arguments": '{"p":1}'}}]}
            )
        ]
    )
    t2 = call(
        [
            chunk(
                {"tool_calls": [{"index": 0, "function": {"name": "read", "arguments": '{"p":2}'}}]}
            )
        ]
    )
    assert t1.tool_calls[0].id != t2.tool_calls[0].id


def test_synthetic_call_id_matches_helper():
    m = call([chunk({"tool_calls": [{"index": 0, "function": {"name": "n", "arguments": "{}"}}]})])
    expected = _synthetic_call_id(0, {"id": "", "name": "n", "args": "{}"})
    assert m.tool_calls[0].id == expected


def test_finish_reason_latest_wins():
    m = call([chunk({}, "length"), chunk({}, "stop")])
    assert m.finish == "stop"


def test_callback_exceptions_do_not_break_the_stream():
    """A UI callback that raises must not abort the turn."""

    def boom(_t):
        raise RuntimeError("render failed")

    m = _stream_chat(
        None,
        "m",
        [],
        None,
        0.2,
        100,
        {},
        on_token=boom,
        transport=lambda p: iter([chunk({"content": "a"}), chunk({"content": "b"})]),
    )
    assert m.content == "ab"


def test_empty_stream_yields_empty_message():
    m = call([])
    assert m.content == "" and m.tool_calls is None and m.finish == ""


# --- non-streaming fallback -------------------------------------------------


class _FakeMsg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeChoice:
    def __init__(self, msg, finish_reason):
        self.message = msg
        self.finish_reason = finish_reason


class _FakeResp:
    def __init__(self, msg, finish_reason):
        self.choices = [_FakeChoice(msg, finish_reason)]


def test_stream_failure_falls_back_to_non_streaming(monkeypatch):
    """A mid-stream error must degrade to one non-streaming call, not raise."""
    calls: list[dict] = []

    def boom(client, params, on_token=None):
        calls.append(params)
        if params.get("stream"):
            raise RuntimeError("connection reset")
        return _FakeResp(_FakeMsg(content="whole", tool_calls=None, reasoning="r"), "stop")

    monkeypatch.setattr("sk.agent._create_with_retry", boom)
    m = _stream_chat(
        None,
        "m",
        [],
        None,
        0.2,
        100,
        {},
        transport=lambda p: (_ for _ in ()).throw(RuntimeError("connection reset")),
    )
    assert m.content == "whole"
    assert m.reasoning == "r"
    assert m.finish == "stop"
    assert any(c.get("stream") is False for c in calls)


def test_fallback_preserves_native_tool_calls(monkeypatch):
    tc = [
        type("T", (), {"id": "n1", "function": type("F", (), {"name": "x", "arguments": "{}"})()})()
    ]
    monkeypatch.setattr(
        "sk.agent._create_with_retry",
        lambda client, p, on_token=None: _FakeResp(
            _FakeMsg(content="", tool_calls=tc, reasoning=""), "stop"
        ),
    )
    m = _stream_chat(None, "m", [], None, 0.2, 100, {}, transport=lambda p: 1 / 0)
    assert m.tool_calls is not None and m.tool_calls[0].id == "n1"


def test_fallback_keeps_empty_tool_list_as_none(monkeypatch):
    """[] and None must stay distinguishable downstream."""
    monkeypatch.setattr(
        "sk.agent._create_with_retry",
        lambda client, p, on_token=None: _FakeResp(
            _FakeMsg(content="x", tool_calls=[], reasoning=""), "stop"
        ),
    )
    m = _stream_chat(None, "m", [], None, 0.2, 100, {}, transport=lambda p: 1 / 0)
    assert m.tool_calls is None


def test_params_carry_tool_choice_and_extra_body():
    seen: list[dict] = []

    def transport(p):
        seen.append(p)
        return iter([])

    _stream_chat(
        None,
        "m",
        [],
        [{"type": "function"}],
        0.5,
        64,
        {"reasoning": {"effort": "high"}},
        transport=transport,
    )
    p = seen[0]
    assert p["stream"] is True
    assert p["tool_choice"] == "auto"
    assert p["max_tokens"] == 64
    assert p["extra_body"] == {"reasoning": {"effort": "high"}}


def test_tool_choice_none_when_no_tools():
    seen: list[dict] = []
    _stream_chat(
        None, "m", [], None, 0.2, 64, {}, transport=lambda p: (seen.append(p), iter([]))[1]
    )
    assert seen[0]["tool_choice"] == "none"


@pytest.mark.parametrize(
    "chunk_in", [None, {}, {"choices": [None]}, {"choices": [{"delta": None}]}]
)
def test_stream_survives_malformed_chunks(chunk_in):
    m = call([chunk_in, chunk({"content": "ok"}, "stop")])
    assert m.content == "ok"
    assert m.finish == "stop"
