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


def test_recap_still_returns_three_lines(monkeypatch):
    """The three-line contract is unchanged when the model answers."""
    _, out = _run_recap(monkeypatch, [], reply="Accomplished: x\nBlocked: y\nNext: z")
    assert out == "Accomplished: x\nBlocked: y\nNext: z"


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
