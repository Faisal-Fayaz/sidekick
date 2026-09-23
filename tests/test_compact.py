"""Compaction tests: estimator, shaping, watermarks, migration, wiring. No network."""

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


def test_passthrough_under_budget():
    msgs = _turns(2)
    seen = []
    prompt, new_summary = compact_history("", msgs, 100000, lambda t: seen.append(t) or "X")
    assert new_summary is None and seen == []
    assert [(m["role"], m["content"]) for m in prompt] == [(m["role"], m["content"]) for m in msgs]
    assert all(set(m) == {"role", "content"} for m in prompt)  # ids stripped


def test_over_budget_shape():
    msgs = _turns(10)
    captured = []
    prompt, new_summary = compact_history(
        "", msgs, 600, lambda t: captured.append(t) or "ROLLED SUMMARY"
    )
    assert new_summary == "ROLLED SUMMARY"
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

    prompt, new_summary = compact_history("", msgs, 800, faithful)
    assert new_summary == "SUMMARY WITH BANANA"
    blob = "\n".join(m["content"] for m in prompt)
    assert "BANANA" in blob


def test_summarizer_failure_falls_back():
    msgs = _turns(10)

    def boom(t):
        raise RuntimeError("model down")

    prompt, new_summary = compact_history("", msgs, 400, boom)
    assert new_summary is None
    assert prompt[0]["role"] == "user"  # anchor
    assert len(prompt) < len(msgs)  # still trimmed


def test_watermark_advances_only_over_new(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    for i in range(10):
        store.save_message("s", "user", f"q{i} with filler words to add bulk " * 6)
        store.save_message("s", "assistant", f"a{i} with filler words to add bulk " * 6)
    seen_inputs = []
    first = prepare_history("s", [], _cfg(), lambda t: seen_inputs.append(t) or "SUM1")
    assert any("Session summary" in m["content"] for m in first)
    summary, up_to = store.get_summary("s")
    assert summary == "SUM1" and up_to == 20
    # eight more turns: summarizer must NOT see already-covered ids
    for i in range(8):
        store.save_message("s", "user", f"fresh topic ZZZ turn {i} with filler " * 8)
        store.save_message("s", "assistant", f"fresh reply ZZZ turn {i} with filler " * 8)
    seen_inputs.clear()
    prepare_history("s", [], _cfg(), lambda t: seen_inputs.append(t) or "SUM2")
    assert store.get_summary("s")[1] == 36
    assert seen_inputs and "q0 with" not in seen_inputs[0]
    assert "ZZZ" in seen_inputs[0] and "SUM1" in seen_inputs[0]  # prior merged in


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
    assert summary == "COMPACTED SUMMARY" and up_to == 26
