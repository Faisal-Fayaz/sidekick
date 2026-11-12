"""The provider seam: one turn loop, many backends.

``sk.agent`` used to own the loop for OpenAI-compatible endpoints while
``sk.anthropic_backend`` re-implemented it for the native Anthropic Messages
API. Two copies of the same loop meant a feature could land on one side only:
the text-JSON tool fallback, the reasoning-only continuation, and post-edit
verification all existed on one path and not the other (#312).

This module is the seam. A :class:`Backend` turns OpenAI-shaped history into a
provider request and a provider response back into a :class:`Turn`. The loop in
``sk.agent`` drives it, so both providers get every feature the loop has, and a
new provider is a new class rather than a new copy of the loop.

The history stays in OpenAI shape on purpose: the Anthropic backend already
knows how to translate it (``openai_messages_to_anthropic``), so the driver
never has to know which provider it is talking to.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from .toolcalls import prompt_echo, strip_control_leak, strip_recap_leak


@dataclass
class FunctionCall:
    """The function half of a tool call, in OpenAI's shape."""

    name: str
    arguments: str  # JSON text, like OpenAI's function.arguments


@dataclass
class ToolCall:
    """One tool call the model asked for."""

    id: str
    function: FunctionCall


@dataclass
class Turn:
    """One model response, normalized across providers.

    The attribute names match the OpenAI message shape on purpose: the driver
    reads them the same way regardless of which backend produced the turn.
    """

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish: str = "stop"  # stop | length | tool_calls
    reasoning: str = ""


def effort_level(cfg, plan_mode: bool = False) -> str:
    """Normalized reasoning level: plan mode forces high, else the configured
    level (garbage falls back to low via config parsing). Never raises.

    Both backends need it — the OpenAI path spells it per provider in the
    request body, the Anthropic path sizes a thinking budget from it — so it
    lives here rather than in one backend.
    """
    try:
        from .config import REASONING_EFFORTS

        level = "high" if plan_mode else str(getattr(cfg, "reasoning_effort", "low") or "low")
        level = level.strip().lower()
        return level if level in REASONING_EFFORTS else "low"
    except Exception:
        return "low"


class Backend(Protocol):
    """What the driver needs from a provider."""

    name: str

    def max_tokens(self) -> int:
        """The per-turn generation cap for this model and provider."""

    def stream_turn(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        *,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> Turn:
        """One model call. Content deltas stream to ``on_token``."""

    def complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> str:
        """One tool-free model call, used for the exhaustion recap."""


def backend_for(cfg, plan_mode: bool = False) -> Backend:
    """The backend for this config. Never raises."""
    if str(getattr(cfg, "provider", "") or "") == "anthropic":
        return AnthropicBackend(cfg, plan_mode=plan_mode)
    # The OpenAI transport lives beside the loop it serves, so it is imported
    # here rather than moved: a backend must not reach into another module's
    # privates to reuse them.
    from .agent import OpenAIBackend

    return OpenAIBackend(cfg, plan_mode=plan_mode)


class AnthropicBackend:
    """The native Anthropic Messages API.

    History arrives in OpenAI shape and is translated per request; responses
    come back as Anthropic content blocks and are normalized into a `Turn`.
    """

    name = "anthropic"

    def __init__(self, cfg, plan_mode: bool = False):
        self.cfg = cfg
        self.plan_mode = plan_mode

    def max_tokens(self) -> int:
        from .model_profiles import max_tokens_for

        return max_tokens_for(self.cfg.model, self.cfg.provider)

    def _payload(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        max_tokens: int,
    ) -> dict:
        """Build a Messages API payload from OpenAI-shaped history."""
        from .anthropic_backend import (
            _cache_breakpoints,
            _thinking_params,
            openai_messages_to_anthropic,
            openai_tools_to_anthropic,
        )

        cfg = self.cfg
        system, anth_messages = openai_messages_to_anthropic(messages)
        payload: dict = {
            "model": cfg.model,
            "max_tokens": max_tokens,
            "messages": anth_messages,
        }
        thinking = _thinking_params(effort_level(cfg, self.plan_mode), max_tokens)
        if thinking is not None:
            # Anthropic accepts temperature only as 1 when thinking is enabled;
            # any other value is a hard 400, so a configured 0.2 used to make
            # every extended-thinking turn fail.
            payload["thinking"] = thinking
            payload["temperature"] = 1
        else:
            payload["temperature"] = cfg.temperature
        system_payload, anth_tools = _cache_breakpoints(
            system, openai_tools_to_anthropic(tools or [])
        )
        if system_payload:
            payload["system"] = system_payload
        if anth_tools:
            payload["tools"] = anth_tools
        return payload

    def stream_turn(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        *,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> Turn:
        from .anthropic_backend import _CountingSink, _post, _stream, _synthetic_block_id

        cfg = self.cfg
        payload = self._payload(messages, tools, self.max_tokens())
        emitted = _CountingSink(on_token)
        try:
            blocks, stop_reason = _stream(
                cfg.effective_base_url(),
                cfg.effective_api_key(),
                payload,
                emitted,
                on_reasoning,
            )
            streamed = True
        except Exception:
            # Streaming failed: retry once without streaming. The answer must
            # not be delivered twice, so the fallback text is only emitted when
            # nothing reached the user (#312).
            streamed = False
            resp = _post(cfg.effective_base_url(), cfg.effective_api_key(), payload)
            blocks = resp.get("content", []) if isinstance(resp, dict) else []
            stop_reason = resp.get("stop_reason", "") if isinstance(resp, dict) else ""

        texts = [
            b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
        ]
        uses = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
        thinking = [
            str(b.get("thinking", "") or "")
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "thinking"
        ]
        text = "".join(texts).strip()
        if not streamed and text and on_token is not None and emitted.count == 0:
            try:
                on_token(text)  # nothing reached the user: emit the whole answer once
            except Exception:
                pass

        calls = []
        for index, use in enumerate(uses):
            args = use.get("input") if isinstance(use.get("input"), dict) else {}
            calls.append(
                ToolCall(
                    # An id-less tool_use block yields tool_use_id="" on the way
                    # back, which is a hard 400. Synthesise a stable one.
                    id=str(use.get("id") or "") or _synthetic_block_id(index, use),
                    function=FunctionCall(
                        name=str(use.get("name", "") or ""),
                        arguments=json.dumps(args),
                    ),
                )
            )
        finish = "length" if stop_reason == "max_tokens" else ("tool_calls" if calls else "stop")
        return Turn(
            content=text,
            tool_calls=calls,
            finish=finish,
            reasoning="".join(thinking).strip(),
        )

    def complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> str:
        from .anthropic_backend import _post

        cfg = self.cfg
        payload = self._payload(messages, None, min(int(max_tokens or 400), 400))
        resp = _post(cfg.effective_base_url(), cfg.effective_api_key(), payload)
        out = "".join(
            b.get("text", "")
            for b in (resp.get("content", []) if isinstance(resp, dict) else [])
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
        if out and on_token is not None:
            try:
                on_token(out)
            except Exception:
                pass
        return out


# --- the exhaustion recap ---------------------------------------------------
#
# The step budget can die without an answer. The recap asks the model for
# three grounded lines and, when nothing ran at all, reports that
# deterministically instead of letting the model invent an accomplishment
# (#369). Both providers share this prompt and this shaping.


def exhaustion_messages(
    messages: list[dict],
    done_work: list[str] | None = None,
    evidence: list[str] | None = None,
) -> list[dict]:
    """The recap request: this turn's history plus one grounded instruction."""
    prior = "; ".join((done_work or [])[:10])
    if prior:
        grounding = (
            "Ground the recap in this list of what actually ran this turn; "
            f"do not claim anything that is not in it:\n{prior}"
        )
    else:
        grounding = (
            "No tool ran this turn — nothing was executed, so the Accomplished "
            "line must say so. Do not describe output you did not produce."
        )
    if evidence:
        grounding += (
            "\n\nThese are the actual outputs of the tools that ran, in order. "
            "Any figure, count, name, or path you report MUST come from these "
            "lines — quote them exactly. If you want a number that is not here, "
            "say so in Blocked; never estimate one:\n" + "\n".join(evidence)
        )
    return list(messages) + [
        {
            "role": "user",
            "content": (
                "You hit the step budget. Reply with ONLY these 3 lines, "
                "filled in, no other text:\n"
                "Accomplished: <one line>\n"
                "Blocked: <one line>\n"
                "Next: <one line>\n\n" + grounding
            ),
        }
    ]


def exhaustion_report(out: str, done_work: list[str] | None = None) -> str:
    """Shape the model's recap into the three-line progress report."""
    text = (out or "").strip()
    prior = list(done_work or [])
    if prior:
        # Work did happen: keep the model's recap, but never let it echo the
        # harness scaffolding back at the user.
        return strip_recap_leak(strip_control_leak(text))

    # Nothing ran, so there is nothing for the model to summarize. Asking it
    # anyway invited it to narrate the prompt and invent an accomplishment
    # (observed 2026-10-11: "Accomplished: Generated 5 project ideas" with an
    # empty ledger). The honest report is deterministic text; the model is
    # asked only because it may know *why* it looped.
    reason = ""
    try:
        # Only a non-echo tail may be quoted back, and only as the reason it
        # looped. An echoed recap would otherwise smuggle the model's
        # fabricated "Accomplished: …" back into the answer.
        if len(text) > 600:
            text = text[-600:]
        if text and not prompt_echo(text):
            reason = text.strip()
    except Exception:
        reason = ""
    lines = ["Stopped: the step budget ran out before an answer."]
    lines.append("Accomplished: nothing — no tool ran this turn.")
    if reason:
        # Drop a leading recap-shaped line from the quoted reason so a stray
        # "Accomplished: X" cannot ride along.
        reason = re.sub(r"(?mi)^\s*(accomplished|blocked|next)\s*:\s*", "", reason).strip()
        reason = re.sub(r"\n+", " ", reason)
    if reason:
        lines.append(f"Blocked: {reason}")
    else:
        lines.append("Blocked: the model spent its steps without producing an answer.")
    lines.append("Next: re-ask, or raise max_steps / switch model with `/model`.")
    return "\n".join(lines)
