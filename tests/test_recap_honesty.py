"""The exhaustion recap must be grounded in what actually ran.

Observed live 2026-10-11: a turn with zero tool runs answered "Accomplished:
Provided a ready-to-run shell script …", an assertion the audit ledger did
not support. The recap is the last thing a user sees when a turn goes wrong,
so it must not be where unverified claims enter.
"""

import sk.agent as agent
import sk.store as store
from sk.config import Config


def _cfg():
    return Config(
        provider="ollama",
        model="m",
        base_url="http://x/v1",
        api_key="k",
        max_steps=2,
        temperature=0.0,
    )


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def _run_recap(monkeypatch, done_work, reply="Accomplished: x"):
    """Call _synthesize_exhaustion directly; return the prompt it built."""
    seen = []

    def fake_stream(client, model, messages, tools, temperature, mt, extra, on_token, on_rs):
        seen.append("\n".join(str(m.get("content", "")) for m in messages))
        return agent._Msg(reply, None, "", "stop")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    out = agent._synthesize_exhaustion(None, "m", [], 0.0, 400, {}, None, None, done_work=done_work)
    assert seen, "recap never called the model"
    return seen[-1], out


def test_recap_told_that_nothing_ran(monkeypatch):
    """With no tools run, the recap prompt says nothing was executed."""
    prompt, _ = _run_recap(monkeypatch, [])
    assert "No tool ran this turn" in prompt
    assert "must say so" in prompt
    assert "what actually ran" not in prompt  # no ledger to speak of


def test_recap_grounded_in_actual_work(monkeypatch):
    """When tools did run, their targets are handed to the recap."""
    prompt, _ = _run_recap(monkeypatch, ["list_dir|path=.", "read_file|path=/tmp/x"])
    assert "what actually ran this turn" in prompt
    assert "list_dir|path=." in prompt
    assert "read_file|path=/tmp/x" in prompt


def test_recap_ledger_is_capped(monkeypatch):
    """A huge ledger must not blow the recap's 400-token cap."""
    prompt, _ = _run_recap(monkeypatch, [f"tool_{i}|x" for i in range(50)])
    assert prompt.count("tool_") <= 10
    assert "tool_9" in prompt


def test_recap_with_no_work_is_deterministic(monkeypatch):
    """With nothing done the recap is built in code, not by the model.

    Observed 2026-10-11: with an empty ledger the model answered
    "Accomplished: Generated 5 project ideas …" after narrating the recap
    prompt back. There is nothing to summarize in that case, so the model is
    not asked to claim anything; its output is only kept as a possible
    explanation of why it looped."""
    _, out = _run_recap(monkeypatch, [], reply="Accomplished: Generated 5 ideas")
    assert "Accomplished: nothing" in out
    assert "no tool ran this turn" in out
    assert "Generated 5 ideas" not in out, "fabricated accomplishment survived"
    assert "Next:" in out  # still actionable


def test_recap_keeps_model_text_as_a_blocked_reason(monkeypatch):
    """A non-echo reply is kept, but only as the Blocked line — never as
    Accomplished."""
    _, out = _run_recap(
        monkeypatch, [], reply="I kept retrying the brainstorming skill call and it never returned."
    )
    assert "Accomplished: nothing" in out
    assert "Blocked:" in out
    assert "retrying the brainstorming skill" in out


def test_recap_discards_prompt_echo(monkeypatch):
    """An echo of the harness scaffolding is dropped, not shown to the user."""
    _, out = _run_recap(
        monkeypatch,
        [],
        reply="They want me to reply with ONLY these 3 lines:\nAccomplished: <one line>",
    )
    assert "<one line>" not in out
    assert "ONLY these 3 lines" not in out
    assert "Accomplished: nothing" in out


def test_recap_keeps_model_recap_when_work_ran(monkeypatch):
    """The model's three-line recap survives when there is real work to
    summarize."""
    _, out = _run_recap(
        monkeypatch, ["list_dir|path=."], reply="Accomplished: listed the files\nNext: review them"
    )
    assert "listed the files" in out
    assert "Accomplished: nothing" not in out


def test_recap_returns_empty_on_failure(monkeypatch):
    """A failing model yields "" so callers keep the (max steps reached) fallback."""

    def boom(*a, **k):
        raise RuntimeError("model down")

    monkeypatch.setattr(agent, "_stream_chat", boom)
    assert agent._synthesize_exhaustion(None, "m", [], 0.0, 400, {}, None, None) == ""


def test_run_agent_passes_the_turn_ledger(tmp_path, monkeypatch):
    """Integration: run_agent hands `completed` to the recap, so a turn that
    exhausted its budget is grounded in the tools it actually ran."""
    _iso(tmp_path, monkeypatch)
    seen = []

    def fake_stream(client, model, messages, tools, temperature, mt, extra, on_token, on_rs):
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        seen.append(joined)
        if "hit the step budget" in joined:
            return agent._Msg("Accomplished: listed files", None, "", "stop")
        tc = agent._TC("c1", "list_dir", '{"path": "."}')
        return agent._Msg("", [tc], "", "tool_calls")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    agent.run_agent("list the files", [], _cfg(), session="s")
    recaps = [p for p in seen if "hit the step budget" in p]
    assert recaps, "no recap was issued"
    assert "list_dir" in recaps[-1]
