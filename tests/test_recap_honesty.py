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


def _run_recap(monkeypatch, done_work, reply="Accomplished: x", evidence=None):
    """Call _synthesize_exhaustion directly; return the prompt it built."""
    seen = []

    def fake_stream(client, model, messages, tools, temperature, mt, extra, on_token, on_rs):
        seen.append("\n".join(str(m.get("content", "")) for m in messages))
        return agent._Msg(reply, None, "", "stop")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    out = agent._synthesize_exhaustion(
        None, "m", [], 0.0, 400, {}, None, None, done_work=done_work, evidence=evidence
    )
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


# --- #369: the recap must see what the tools *returned*, not just what ran -----
# Session tui-20261003-112714365610-0001. `exec` ran `wc -l` successfully, the
# real 5,461 was in context, and the recap still reported a "roughly 240,000-line
# source footprint" — a ~44x overstatement of a real project, in the one field
# someone sizes a porting effort by. The target ledger named the command but not
# its output, so nothing anchored the number.


def test_evidence_anchors_figures_in_the_recap_prompt(monkeypatch):
    done = ["exec|cmd=wc -l src/*#f7aabdfc4f0d"]
    evidence = [
        "exec|cmd=wc -l src/*#f7aabdfc4f0d ->  120 main.cpp\n 5341 physics.cpp\n 5461 total"
    ]
    prompt, _ = _run_recap(monkeypatch, done, evidence=evidence)
    assert "5461 total" in prompt, "the real line count must reach the recap"
    assert "never estimate one" in prompt


def test_record_completed_collects_output_per_tool(monkeypatch):
    completed, evidence = [], []
    batch = [
        ("exec", {"cmd": "ls"}),
        ("exec", {"cmd": "wc -l src/*"}),
        ("read_file", {"path": "src/physics.hpp"}),
    ]
    outs = [
        ("main.cpp physics.cpp CMakeLists.txt", True),
        ("  5461 total", True),
        ("struct Body { float x; };", True),
    ]
    agent._record_completed(completed, batch, outs, evidence)
    assert len(completed) == 3
    assert any("5461 total" in e for e in evidence)
    # target and output stay paired on one line
    assert all("->" in e for e in evidence)
    # multiline output collapses so it reads as one quotable line
    assert not any("\n" in e for e in evidence)


def test_failed_tool_output_is_still_offered_as_evidence(monkeypatch):
    """A failed call stays out of the target ledger, but the recap's Blocked line
    needs its actual error text or it will invent a reason (#369 did exactly that:
    a real error beside a fabricated number)."""
    completed, evidence = [], []
    agent._record_completed(
        completed,
        [("exec", {"cmd": "wc -l"})],
        [("Error: shell did not expand the patterns", False)],
        evidence,
    )
    assert completed == [], "failed call must not count as accomplished work"
    assert any("did not expand" in e for e in evidence)


def test_evidence_is_capped():
    """The recap call is budgeted at 400 tokens, so evidence cannot grow with
    the number of tools a turn ran."""
    evidence = []
    for i in range(200):
        agent._record_completed(
            [], [("exec", {"cmd": f"cmd{i}"})], [(f"output {i} " + "x" * 4000, True)], evidence
        )
    assert 0 < len(evidence) < 200
    assert sum(len(e) for e in evidence) <= agent.RECAP_EVIDENCE_TOTAL + 400


def test_record_completed_never_raises_without_evidence_arg(monkeypatch):
    """Existing two-arg callers (and _error_with_progress's contract) still hold."""
    completed = []
    agent._record_completed(completed, [("exec", {"cmd": "ls"})], [("out", True)])
    assert completed == ["exec|cmd=ls#6a8f0c9e1b2d"] or completed[0].startswith("exec|cmd=ls")
