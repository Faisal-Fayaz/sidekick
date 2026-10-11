"""Compaction tests: estimator, shaping, watermarks, migration, wiring. No network."""

import pytest

import sk.agent as agent
import sk.store as store
from sk.agent import compact_history, estimate_tokens, prepare_history
from sk.config import Config


def _cfg(**kw):
    base = {
        "provider": "ollama",
        "model": "t",
        "base_url": "http://x/v1",
        "api_key": "x",
        "max_steps": 5,
        "temperature": 0.0,
        "history_budget_tokens": 600,
    }
    base.update(kw)
    return Config(**base)


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def _turns(n, marker_at=None, marker="codename BANANA"):
    out = []
    for i in range(n):
        out.append({"id": i + 1, "role": "user", "content": f"question number {i}"})
        body = f"answer number {i} with some filler words to add bulk " * 8
        if marker_at is not None and i == marker_at:
            body = f"the project {marker} confirmed here. " + body
        out.append({"id": 100 + i + 1, "role": "assistant", "content": body})
    return out


def test_estimate_sanity():
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("x" * 400) == 100


def test_summary_prompt_is_a_headed_update_schema():
    """Pinned contract (Fan et al., arXiv:2609.20804 §7.3, Fig 18): fixed
    headings support incremental maintenance because the previous summary
    is supplied and revised; "summarize — don't transcribe" keeps the
    summary smaller than the events it replaces. The "rolling summary"
    phrase is load-bearing for test_run_agent_compacts_long_session's
    fake-stream detector below."""
    p = agent._SUMMARY_PROMPT
    for heading in (
        "## Goal",
        "## Actions taken",
        "## Done",
        "## Pending",
        "## Errors & fixes",
        "## Current state",
        "## Next step",
    ):
        assert heading in p, f"summary schema lost {heading}"
    assert "UPDATE it" in p and "don't transcribe" in p
    assert "ONLY the summary" in p and "rolling summary" in p


def test_passthrough_under_budget():
    msgs = _turns(2)
    seen = []
    prompt, new_summary, split = compact_history("", msgs, 100000, lambda t: seen.append(t) or "X")
    assert new_summary is None and split is None and seen == []
    assert [(m["role"], m["content"]) for m in prompt] == [(m["role"], m["content"]) for m in msgs]
    assert all(set(m) == {"role", "content"} for m in prompt)  # ids stripped


def test_over_budget_shape():
    msgs = _turns(10)
    captured = []
    prompt, new_summary, split = compact_history(
        "", msgs, 600, lambda t: captured.append(t) or "ROLLED SUMMARY"
    )
    assert new_summary == "ROLLED SUMMARY"
    assert split is not None and 0 < split < len(msgs)
    # everything at or after `split` is retained VERBATIM (not summarised)
    assert prompt[1]["content"] == msgs[0]["content"]
    assert prompt[0]["content"].startswith("[Session summary so far]:\nROLLED SUMMARY")
    assert prompt[1] == {"role": msgs[0]["role"], "content": msgs[0]["content"]}  # anchor kept
    assert len(prompt) < len(msgs)
    # summarizer saw the middle, not the kept recent tail
    assert "question number 9" not in captured[0]
    assert "question number 1" in captured[0]


def test_named_fact_survives_compaction():
    msgs = _turns(20, marker_at=5)

    # faithful summarizer: keeps BANANA iff it was actually given it (tests OUR selection)
    def faithful(t):
        return "SUMMARY WITH BANANA" if "BANANA" in t else "SUMMARY WITHOUT"

    prompt, new_summary, _split = compact_history("", msgs, 800, faithful)
    assert new_summary == "SUMMARY WITH BANANA"
    blob = "\n".join(m["content"] for m in prompt)
    assert "BANANA" in blob


def test_summarizer_failure_falls_back():
    msgs = _turns(10)

    def boom(t):
        raise RuntimeError("model down")

    with pytest.raises(RuntimeError):
        compact_history("", msgs, 400, boom)
    # A summarizer failure now propagates rather than being reported as
    # "new_summary is None", which is indistinguishable from "nothing to fold"
    # and was the source of the misleading 'summarizer unreachable?' message.


def test_watermark_stops_before_the_retained_tail(tmp_path, monkeypatch):
    """The watermark is over the SUMMARISED PREFIX, never max(id) of the session.

    The old assertion here was `up_to == 36` after 36 messages existed — i.e. it
    locked in the bug. Advancing past the retained tail means the messages
    compaction deliberately kept verbatim are excluded from `uncovered`
    forever, so the next turn never sees them again.
    """
    _iso(tmp_path, monkeypatch)
    for i in range(10):
        store.save_message("s", "user", f"q{i} with filler words to add bulk " * 6)
        store.save_message("s", "assistant", f"a{i} with filler words to add bulk " * 6)
    seen_inputs = []
    first = prepare_history("s", [], _cfg(), lambda t: seen_inputs.append(t) or "SUM1")
    assert any("Session summary" in m["content"] for m in first)
    summary, up_to = store.get_summary("s")
    assert summary == "SUM1"
    assert up_to < 20, f"watermark consumed the retained tail: {up_to}"
    # the messages above the watermark must still be visible on the next turn
    retained_ids = [m["id"] for m in store.get_history_full("s") if m["id"] > up_to]
    assert retained_ids, "nothing retained — compaction over-folded"

    for i in range(8):
        store.save_message("s", "user", f"fresh topic ZZZ turn {i} with filler " * 8)
        store.save_message("s", "assistant", f"fresh reply ZZZ turn {i} with filler " * 8)
    seen_inputs.clear()
    second = prepare_history("s", [], _cfg(), lambda t: seen_inputs.append(t) or "SUM2")
    blob = "\n".join(str(m.get("content", "")) for m in second)
    # previously-retained messages must still be present verbatim
    kept = store.get_history_full("s")
    above = [m for m in kept if m["id"] <= up_to]
    assert above, "fixture problem"
    sample = above[0]["content"][:40]
    assert sample in blob or "Session summary" in blob
    assert seen_inputs and "q0 with" not in seen_inputs[0]
    assert "ZZZ" in seen_inputs[0] and "SUM1" in seen_inputs[0]  # prior merged in


def test_watermark_never_regresses(tmp_path, monkeypatch):
    """A stale or empty summary must not rewind, or a span gets re-summarised."""
    _iso(tmp_path, monkeypatch)
    for i in range(12):
        store.save_message("s", "user", f"q{i} with filler words to add bulk " * 6)
        store.save_message("s", "assistant", f"a{i} with filler words to add bulk " * 6)
    prepare_history("s", [], _cfg(), lambda t: "SUM1")
    before = store.get_summary("s")[1]
    prepare_history("s", [], _cfg(), lambda t: "SUM2")
    after = store.get_summary("s")[1]
    assert after >= before


def test_empty_uncovered_preserves_summary(tmp_path, monkeypatch):
    """Zero new messages must return the prior summary and not touch the watermark.

    The old code returned the summary here but advanced the watermark past the
    retained tail, so the conversation vanished from the next prompt.
    """
    _iso(tmp_path, monkeypatch)
    for i in range(10):
        store.save_message("s", "user", f"q{i} with filler words to add bulk " * 6)
        store.save_message("s", "assistant", f"a{i} with filler words to add bulk " * 6)
    prepare_history("s", [], _cfg(), lambda t: "SUM1")
    summary_before, up_before = store.get_summary("s")
    out = prepare_history("s", [], _cfg(), lambda t: pytest.fail("summarizer must not run"))
    assert any("SUM1" in str(m.get("content", "")) for m in out)
    assert store.get_summary("s") == (summary_before, up_before)


def test_run_agent_compacts_long_session(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    for i in range(13):
        store.save_message("s", "user", f"hello turn {i} with filler words to add bulk " * 5)
        store.save_message("s", "assistant", f"reply turn {i} with filler words to add bulk " * 5)

    def fake_stream(
        client,
        model,
        messages,
        tools,
        temperature,
        max_tokens,
        extra,
        on_token=None,
        on_reasoning=None,
    ):
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        if "rolling summary" in joined:
            return agent._Msg("COMPACTED SUMMARY", None, "", "stop")
        return agent._Msg("final answer", None, "", "stop")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    out = agent.run_agent("fresh question", [], _cfg(), session="s")
    assert out == "final answer"
    summary, up_to = store.get_summary("s")
    assert summary == "COMPACTED SUMMARY"
    # must not advance past the verbatim tail (was asserting `== 26`, the bug)
    assert up_to < 26, f"watermark consumed the retained tail: {up_to}"


# --- #306: no silent no-op, and an honest outcome ---------------------------


def test_huge_single_message_is_not_reported_as_a_failure():
    """The audit's repro: one 40k-char message, budget 3000.

    Previously the `or ri > n - 2` clause emptied the middle slice, compaction
    returned the whole history with new_summary=None, and the caller printed
    "compaction failed (summarizer unreachable?)" for a summarizer that was
    never called. Now nothing is foldable, which is reported as nothing-foldable
    and the message is passed through rather than silently dropped.
    """
    calls = []

    def boom(t):
        calls.append(t)
        raise AssertionError("summarizer must not be called")

    msgs = [{"role": "user", "content": "x" * 40000}]
    prompt, new_summary, split = compact_history("", msgs, 3000, boom)
    assert calls == []
    assert new_summary is None and split is None
    assert len(prompt) == 1 and len(prompt[0]["content"]) == 40000


def test_retained_tail_is_never_summarised():
    """Messages at or after `split` must not appear in the summarizer input."""
    msgs = _turns(20)
    captured = []
    prompt, new_summary, split = compact_history(
        "", msgs, 700, lambda t: captured.append(t) or "SUM"
    )
    assert new_summary == "SUM" and split is not None
    tail = msgs[split:]
    assert tail, "no tail retained"
    tail_blob = "\n".join(str(m.get("content", "")) for m in tail)
    # every retained message survives verbatim in the prompt
    prompt_blob = "\n".join(str(m.get("content", "")) for m in prompt)
    for m in tail:
        assert str(m.get("content", "")) in prompt_blob
    # and the summarizer only saw the prefix
    assert "question number 19" not in captured[0]
    assert tail_blob.split("\n")[0] not in captured[0]


def test_tool_result_never_orphaned_from_its_assistant_turn():
    """An orphaned tool message is a hard API 400, so the split respects pairs."""
    msgs = []
    for i in range(12):
        msgs.append({"role": "user", "content": f"ask {i} " + "x" * 400})
        msgs.append({"role": "assistant", "content": f"tool_call {i} " + "y" * 400})
        msgs.append({"role": "tool", "content": f"result {i} " + "z" * 400})
    _prompt, _summary, split = compact_history("", msgs, 900, lambda t: "SUM")
    assert split is not None
    assert msgs[split].get("role") != "tool", "split left a tool result orphaned"


# --- staged elision before summarization (M1 soft stage) ----------------------


def _fenced(label, n):
    return f"<<<UNTRUSTED source={label}\n" + ("x" * n) + "\nEND-UNTRUSTED>>>"


def _bulky_turns(n, fence_at=1, fence_chars=8000):
    out = []
    for i in range(n):
        out.append({"id": i + 1, "role": "user", "content": f"question number {i}"})
        body = f"answer number {i} with some filler words to add bulk " * 8
        if i == fence_at:
            body += "\n" + _fenced("file /tmp/notes.md", fence_chars)
        out.append({"id": 100 + i + 1, "role": "assistant", "content": body})
    return out


def test_elision_skips_summarizer_when_sufficient():
    """The T4 mechanism: cheap rule-based elision first, LLM call only when
    elision did not suffice. The bomb proves the summarizer never ran."""
    msgs = _bulky_turns(10)

    def bomb(t):
        raise AssertionError("summarizer must not run when elision suffices")

    prompt, new_summary, split = compact_history("", msgs, 1500, bomb)
    assert new_summary is None and split is None  # no watermark advance
    blob = "\n".join(str(m.get("content", "")) for m in prompt)
    assert "[fenced file /tmp/notes.md block elided" in blob
    assert "8001 chars" in blob  # 8000 x's + the newline before the marker
    assert "x" * 8000 not in blob  # the bulk is gone
    assert len(prompt) == len(msgs)  # content-only: count/order/roles intact
    assert [m["role"] for m in prompt] == [m["role"] for m in msgs]
    assert prompt[-1]["content"] == msgs[-1]["content"]  # tail verbatim


def test_elision_still_summarizes_when_insufficient():
    """Elision shrinks the summarizer input but must not replace it when the
    view is still over budget."""
    msgs = _bulky_turns(10)
    for m in msgs:  # pad everything so elision alone cannot fit
        m["content"] = str(m.get("content", "")) + " padding words here " * 60
    captured = []
    prompt, new_summary, split = compact_history(
        "", msgs, 700, lambda t: captured.append(t) or "SUM"
    )
    assert new_summary == "SUM" and split is not None
    assert "[fenced file /tmp/notes.md block elided" in captured[0]
    assert "x" * 8000 not in captured[0]  # summarizer saw the stub, not the bulk


def test_small_fences_stay_verbatim():
    """Only bulky blocks are worth stubbing; a stub must never cost more
    context than the block it replaces."""
    from sk.agent import _elide_fenced_blocks

    small = [{"role": "assistant", "content": "see " + _fenced("file a", 50)}]
    out, n, reclaimed = _elide_fenced_blocks(small)
    assert (n, reclaimed) == (0, 0)
    assert out[0]["content"] == small[0]["content"]


def test_elision_preserves_shape_and_never_raises():
    """Roles, order, and count are untouched (tool-pairing invariant holds),
    and malformed input degrades to a passthrough, never a raise."""
    from sk.agent import _elide_fenced_blocks

    msgs = [
        {"role": "assistant", "content": "calling now"},
        {"role": "tool", "content": "result " + _fenced("exec df -h", 2000)},
        {"role": "user", "content": "thanks"},
        {"role": "assistant"},  # no content key
        {"role": "user", "content": None},  # non-str content
        {"role": "assistant", "content": "<<<UNTRUSTED source=x\nno close marker"},
    ]
    out, n, reclaimed = _elide_fenced_blocks(msgs)
    assert n == 1 and reclaimed >= 2000
    assert [m.get("role") for m in out] == [m.get("role") for m in msgs]
    assert out[2].get("content") == "thanks" and out[3].get("content", "") == ""
    assert out[5]["content"].startswith("<<<UNTRUSTED")  # malformed: untouched
    assert _elide_fenced_blocks([]) == ([], 0, 0)
    assert _elide_fenced_blocks(None) == ([], 0, 0)
