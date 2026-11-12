"""One table, both provider paths (#312, #434).

The two paths used to drift: a fix landed on the OpenAI path and the Anthropic
path kept the old behaviour, with nothing to say so. Each row below states one
scenario and one expectation that must hold for **every** provider, so a fix
cannot land on one side only.

Adding a provider means adding a driver to this file, not a new test file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

import pytest

import sk.agent as agent
import sk.anthropic_backend as ab
import sk.store as store
from sk.config import Config


@dataclass(frozen=True)
class Response:
    """One model response, in provider-neutral terms.

    `tools` is a tuple of (call id, tool name, arguments). `finish="length"`
    means the provider cut the answer off mid-thought.
    """

    text: str = ""
    thinking: str = ""
    tools: tuple[tuple[str, str, dict], ...] = ()
    finish: str = "stop"


@dataclass
class Ctx:
    """What a scenario observed, per provider."""

    out: str = ""
    requests: list[dict] = field(default_factory=list)
    batch: list[tuple[str, dict]] = field(default_factory=list)
    verified: list[list[str]] = field(default_factory=list)
    tool_result: str = ""


# --- providers ------------------------------------------------------------


def _openai_cfg() -> Config:
    return Config(
        provider="ollama",
        model="t",
        base_url="http://x/v1",
        api_key="x",
        max_steps=6,
        max_steps_custom=True,
        temperature=0.0,
    )


def _anthropic_cfg() -> Config:
    return Config(
        provider="anthropic",
        model="claude-x",
        base_url="https://api.anthropic.test",
        api_key="k",
        max_steps=6,
        max_steps_custom=True,
        temperature=0.2,
    )


def _run_openai(monkeypatch, script: list[Response], ctx: Ctx) -> str:
    state = {"i": 0}

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
        ctx.requests.append({"messages": list(messages)})
        resp = script[min(state["i"], len(script) - 1)]
        state["i"] += 1
        calls = None
        if resp.tools:
            calls = [agent._TC(cid, name, json.dumps(args)) for cid, name, args in resp.tools]
        finish = "length" if resp.finish == "length" else ("tool_calls" if calls else "stop")
        return agent._Msg(resp.text, calls, resp.thinking, finish)

    monkeypatch.setattr(agent, "_stream_chat", fake_stream)
    return agent.run_agent("task", [], _openai_cfg(), approve=lambda n, a: True, session="parity")


def _run_anthropic(monkeypatch, script: list[Response], ctx: Ctx) -> str:
    state = {"i": 0}

    def fake_stream(base, key, payload, on_token=None, on_reasoning=None, **k):
        ctx.requests.append({"messages": list(payload.get("messages") or [])})
        resp = script[min(state["i"], len(script) - 1)]
        state["i"] += 1
        blocks: list[dict] = []
        if resp.thinking:
            blocks.append({"type": "thinking", "thinking": resp.thinking})
        if resp.text:
            blocks.append({"type": "text", "text": resp.text})
        for cid, name, args in resp.tools:
            blocks.append({"type": "tool_use", "id": cid, "name": name, "input": args})
        stop = (
            "max_tokens" if resp.finish == "length" else ("tool_use" if resp.tools else "end_turn")
        )
        return (blocks, stop)

    monkeypatch.setattr(ab, "_stream", fake_stream)
    return ab.run_anthropic_agent(
        "task", [], _anthropic_cfg(), approve=lambda n, a: True, session="parity"
    )


PROVIDERS = {"openai": _run_openai, "anthropic": _run_anthropic}


# --- scenarios ------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    script: list[Response]
    check: Callable[[Ctx], None]
    tool_result: str = ""
    verify_verdicts: tuple[str, ...] = ()


def _check_max_tokens_mid_tool_use(ctx: Ctx) -> None:
    assert len(ctx.requests) >= 3, "the cut-off turn did not continue"
    assert ctx.batch, "the tool call was never dispatched"
    assert ctx.batch[0][0] == "list_dir"
    assert ctx.out.strip() == "done"


def _check_reasoning_only(ctx: Ctx) -> None:
    assert len(ctx.requests) >= 2, "a reasoning-only turn did not ask to continue"
    assert ctx.out.strip() == "the answer"


def _check_tool_result_not_truncated(ctx: Ctx) -> None:
    blob = json.dumps(ctx.requests[-1]["messages"])
    assert ctx.tool_result in blob, "the tool result was truncated before the model saw it"


def _check_text_json_tool(ctx: Ctx) -> None:
    assert ctx.batch, "the text-JSON tool call was never dispatched"
    assert ctx.batch[0][0] == "list_dir"
    assert ctx.out.strip() == "listed"


def _check_verify_repair_round(ctx: Ctx) -> None:
    assert ctx.verified, "post-edit verification never ran"
    assert ctx.verified[0], "verification ran with no edited files"
    assert len(ctx.requests) >= 3, "a failing check did not buy a repair round"
    assert ctx.out.strip() == "fixed"


def _check_verify_unknown_reported(ctx: Ctx) -> None:
    assert ctx.verified, "post-edit verification never ran"
    assert "verification: unknown" in ctx.out, "an unknown verdict was not reported"


SCENARIOS: dict[str, Scenario] = {
    "max_tokens_mid_tool_use": Scenario(
        script=[
            Response(text="I will call a tool. Then", finish="length"),
            Response(tools=(("c1", "list_dir", {"path": "/tmp"}),)),
            Response(text="done"),
        ],
        check=_check_max_tokens_mid_tool_use,
    ),
    "reasoning_only_turn": Scenario(
        script=[
            Response(thinking="thinking about it"),
            Response(text="the answer"),
        ],
        check=_check_reasoning_only,
    ),
    "tool_result_not_truncated": Scenario(
        script=[
            Response(tools=(("c1", "read_file", {"path": "x"}),)),
            Response(text="ok"),
        ],
        check=_check_tool_result_not_truncated,
        tool_result="Z" * 12000,
    ),
    "text_json_tool_fallback": Scenario(
        script=[
            Response(
                text='Listing.\n```json\n{"name": "list_dir", "arguments": {"path": "/tmp"}}\n```'
            ),
            Response(text="listed"),
        ],
        check=_check_text_json_tool,
    ),
    "post_edit_verify_repair_round": Scenario(
        script=[
            Response(tools=(("c1", "write_file", {"path": "app.py", "content": "x = 1"}),)),
            Response(text="done"),
            Response(text="fixed"),
        ],
        check=_check_verify_repair_round,
        verify_verdicts=("fail", "pass"),
    ),
    "post_edit_verify_unknown": Scenario(
        script=[
            Response(tools=(("c1", "write_file", {"path": "app.py", "content": "x = 1"}),)),
            Response(text="done"),
        ],
        check=_check_verify_unknown_reported,
        verify_verdicts=("unknown",),
    ),
}


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_provider_parity(name, provider, tmp_path, monkeypatch):
    scenario = SCENARIOS[name]
    ctx = Ctx(tool_result=scenario.tool_result)

    def fake_batch(batch, *a, **k):
        ctx.batch.extend(batch)
        return [(scenario.tool_result or "ok", True) for _ in batch]

    monkeypatch.setattr(agent, "_run_tools_batch", fake_batch)

    if scenario.verify_verdicts:
        import sk.verify as verify_mod
        from sk.verify import VerificationResult

        verdicts = list(scenario.verify_verdicts)

        def fake_verify(files, **kwargs):
            ctx.verified.append(list(files))
            status = verdicts.pop(0) if verdicts else "pass"
            return VerificationResult(status=status, files=tuple(files))

        monkeypatch.setattr(verify_mod, "verify_files", fake_verify)

    ctx.out = PROVIDERS[provider](monkeypatch, scenario.script, ctx)
    scenario.check(ctx)


def test_parity_table_covers_the_known_gaps():
    """The table is the regression net: every gap #312 lists has a row."""
    expected = {
        "max_tokens_mid_tool_use",
        "reasoning_only_turn",
        "tool_result_not_truncated",
        "text_json_tool_fallback",
        "post_edit_verify_repair_round",
        "post_edit_verify_unknown",
    }
    assert expected <= set(SCENARIOS)


# --- transport-level parity ------------------------------------------------
#
# Two regressions live in the provider transport, not in the loop, so they
# cannot be expressed as a loop scenario: the id of a call the provider sent
# without one, and a transport that dies after the answer already started.


def test_id_less_tool_call_gets_a_stable_id_on_both_paths():
    """An id-less call must not produce an empty tool id on either path."""
    from sk.toolcalls import synthetic_call_id

    openai_id = synthetic_call_id(0, {"name": "list_dir", "args": "{}"})
    anthropic_id = ab._synthetic_block_id(0, {"name": "list_dir"})

    assert openai_id and anthropic_id
    # stable for the same call, so a retried turn reuses the id
    assert openai_id == synthetic_call_id(0, {"name": "list_dir", "args": "{}"})
    assert anthropic_id == ab._synthetic_block_id(0, {"name": "list_dir"})
    # and distinct from each other, so the two paths never collide
    assert openai_id != anthropic_id


def test_a_transport_that_dies_mid_answer_never_delivers_it_twice(monkeypatch, tmp_path):
    """A stream that fails after the answer started must not repeat the answer.

    Anthropic falls back to a non-streaming call and must not re-emit what the
    user already saw. The OpenAI path fails the turn outright instead of
    pretending it has an answer.
    """
    # --- Anthropic ---
    seen: list[str] = []

    def fake_stream(base, key, payload, on_token=None, on_reasoning=None, **k):
        if on_token is not None:
            on_token("already streamed")
        raise RuntimeError("stream error: upstream reset")

    def fake_post(url, key, payload, timeout=300.0):
        return {"content": [{"type": "text", "text": "already streamed"}]}

    monkeypatch.setattr(ab, "_stream", fake_stream)
    monkeypatch.setattr(ab, "_post", fake_post)
    out = ab.run_anthropic_agent(
        "hello", [], _anthropic_cfg(), on_token=seen.append, session="parity"
    )
    assert seen == ["already streamed"], f"the answer was delivered twice: {seen}"
    assert "already streamed" in out

    # --- OpenAI ---
    seen2: list[str] = []

    def fake_stream_chat(
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
        if on_token is not None:
            on_token("already streamed")
        raise RuntimeError("stream error: upstream reset")

    monkeypatch.setattr(agent, "_stream_chat", fake_stream_chat)
    with pytest.raises(RuntimeError):
        agent.run_agent(
            "do the thing", [], _openai_cfg(), approve=lambda n, a: True, on_token=seen2.append
        )
    assert seen2 == ["already streamed"], f"the answer was delivered twice: {seen2}"
