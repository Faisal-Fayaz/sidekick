"""Provider-agnostic handling of tool calls that arrive as text.

These helpers used to live as private symbols in :mod:`sk.agent`. The Anthropic
backend needed the same behaviour, and importing ``_``-prefixed names across
modules is exactly how the two provider paths drifted apart (#312): a feature
landed on one path, the other silently kept the old behaviour.

Everything here is pure text handling with no I/O and no provider knowledge, so
both paths can share it without a dependency between them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Container

# Prefix marking loop-machinery instructions (tool-protocol notes, continue
# prompts). They steer the immediate next call only; the caller removes older
# copies so dead instructions stop haunting later turns.
CONTROL_TAG = "[sidekick-control] "

# Verbatim loop instruction for text-only models (llama3.2:3b starts with it
# pre-seeded). Small models echo context back into answers, so the tag and
# this sentence both reached user-visible output verbatim during live probing
# (a fenced "plan-test.txt" containing the tag). `strip_control_leak` removes
# them from every user-facing return; the in-loop messages keep them.
TEXT_ONLY_NOTICE = (
    "[model does not support native tool calling — emit tools as ```json blocks only]"
)

CONTINUE_NOW = "Continue with your answer now."
CONTINUE_TOOLS = "Continue: emit the tool calls now, no more prose."

# How many times one turn may retry after the model emits an unparseable tool
# call before giving up and saying so. Two is enough to catch a one-off slip and
# a model that needed telling; beyond that the format is not going to change,
# and spending the rest of the budget on it would end the turn with less than
# saying plainly that nothing ran.
MAX_RESIDUE_RETRIES = 2

# --- unparsed tool-call residue -------------------------------------------
#
# A model whose tool calls arrive in a chat-template format we do not parse
# (observed: MiniMax's `<invoke name="...">` markup via an OpenRouter free model)
# used to have its markup posted verbatim as the assistant's answer. Two things
# went wrong at once: the user saw model internals, and the calls the model meant
# to make were silently dropped, so the turn ended having done none of them.
#
# `parse_text_tools` only finds JSON, so nothing downstream could catch it — and
# the "emit tools as ```json blocks" note only fires when native tools are OFF,
# which is not the case for a model that usually calls tools natively. There was
# no path at all.
#
# These markers appear nowhere in SYSTEM_PROMPT or TOOLS_SCHEMA (asserted in
# tests), so anchoring on a literal `<` plus a tag name cannot false-positive on
# prose that merely mentions tool_call.
#
# Kept deliberately narrow. `parameter` and `function` were tried and dropped:
# "A <parameter> is a placeholder in some frameworks" is ordinary prose, and a
# detector that eats legitimate answers is worse than none.
_TOOL_RESIDUE_TAGS = ("tool_call", "toolcall", "invoke")
_TAG_RESIDUE = re.compile(r"<\s*/?\s*(?:" + "|".join(_TOOL_RESIDUE_TAGS) + r")\b[^>]*>", re.I)
# Some providers wrap the template in its own delimiters, e.g. `<]minimax[>[<`.
_TEMPLATE_ARTIFACT = re.compile(r"<\]?\s*[\w.-]{2,20}\s*\[>\s*\[<", re.I)
# The `<invoke name="X">` form is residue only when X is a tool we actually have;
# otherwise the model is quoting some *other* system's markup.
_INVOKE_NAME = re.compile(r"<\s*invoke\s+name\s*=\s*[\"']([A-Za-z_][\w.]*)[\"']", re.I)

# Scaffolding from the exhaustion-recap prompt. Observed live 2026-10-11: a
# reasoning model narrated the recap instruction back into its answer ("The
# user is asking me to continue with my answer, but I hit the step budget. They
# want me to reply with ONLY 3 lines … Accomplished: <one line>"), so the user
# saw the harness's own prompt and its deliberation instead of a result. These
# are stripped from user-facing output the same way CONTROL_TAG is.
RECAP_LEAKS = (
    "You hit the step budget.",
    "Reply with ONLY these 3 lines",
    "Accomplished: <one line>",
    "Blocked: <one line>",
    "Next: <one line>",
    "No tool ran this turn",
    "Ground the recap in this list",
    "do not claim anything that is not in it",
)


def balanced_objects(text: str) -> list[str]:
    """Extract top-level {...} spans with balanced braces (handles nesting)."""
    spans: list[str] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    spans.append(text[start : i + 1])
                    start = -1
    return spans


def coerce_args(raw) -> dict | None:
    """Tool arguments as a dict, whatever shape the model sent them in."""
    import json as _json

    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            obj = _json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def match_tool_obj(obj: dict, allowed: Container[str]) -> tuple[str, dict] | None:
    """Match a parsed JSON object against the known tool names."""
    if isinstance(obj, dict) and obj.get("name") in allowed:
        for key in ("arguments", "parameters", "params", "input"):
            if key in obj:
                args = coerce_args(obj.get(key))
                if args is not None:
                    return (obj["name"], args)
        return (obj["name"], {})
    if isinstance(obj, dict) and isinstance(obj.get("function"), dict):
        fn = obj["function"]
        if fn.get("name") in allowed:
            for key in ("arguments", "parameters", "params", "input"):
                if key in fn:
                    args = coerce_args(fn.get(key))
                    if args is not None:
                        return (fn["name"], args)
            return (fn["name"], {})
    return None


def known_tool_names() -> frozenset[str]:
    """Every tool name in the schema, derived rather than restated.

    This was a hand-maintained list of 17 names, duplicated a second time as a
    local `allowed` set inside parse_text_tools. It had already drifted:
    `generate_image` and `shell_session` exist in TOOLS_SCHEMA and were in
    neither copy. Two consequences, both silent:

    - a model emitting either as a ```json block had it discarded, so the tool
      was never called and nothing said why;
    - #368's residue detector only treats `<invoke name=X>` as unparsed markup
      when X is in this set, so markup naming either tool was posted to the user
      verbatim -- the exact failure that change existed to prevent.

    Deriving it means a new tool cannot be half-added. Both excluded tools are
    approval-gated, so routing them through the text-JSON fallback puts them
    behind the same gate as a native call.
    """
    from .tools.registry import TOOLS_SCHEMA

    names: set[str] = set()
    for entry in TOOLS_SCHEMA:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("function")
        if isinstance(fn, dict):
            name = str(fn.get("name", ""))
            if name:
                names.add(name)
    return frozenset(names)


KNOWN_TOOL_NAMES = known_tool_names()


def tool_residue(text: str) -> bool:
    """True when `text` carries an unparsed tool-call attempt. Never raises.

    Conservative by design: a false positive would eat a legitimate answer, so
    this only fires on markup that cannot plausibly be prose.
    """
    try:
        t = text or ""
        if not t:
            return False
        names = {m.group(1) for m in _INVOKE_NAME.finditer(t)}
        if names & KNOWN_TOOL_NAMES:
            return True
        if names:
            # Some other system's markup. Only our template wrapper counts.
            return bool(_TEMPLATE_ARTIFACT.search(t))
        return bool(_TAG_RESIDUE.search(t) or _TEMPLATE_ARTIFACT.search(t))
    except Exception:
        return False


def strip_tool_residue(text: str) -> str:
    """The prose that preceded any residue. Never raises, always returns a str."""
    try:
        t = text or ""
        if not tool_residue(t):
            return t
        # cut from the first marker; keep whatever came before it
        starts = []
        m = _INVOKE_NAME.search(t)
        if m:
            starts.append(m.start())
        for rx in (_TAG_RESIDUE, _TEMPLATE_ARTIFACT):
            g = rx.search(t)
            if g:
                starts.append(g.start())
        if not starts:
            return t
        # Trim the delimiter debris too: the real markup often starts with the
        # template's own bracket, which would otherwise be left sitting in front
        # of the error we report.
        return t[: min(starts)].strip().strip("[]<>").strip()
    except Exception:
        return (text or "").strip()


def parse_text_tools(text: str) -> list[tuple[str, dict]]:
    """Parse ALL tool JSON objects in text (fenced or bare, any args key).

    Returns list of (name, args). Only allows known tools.
    """
    allowed = KNOWN_TOOL_NAMES
    found: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for span in balanced_objects(text):
        try:
            obj = json.loads(span)
        except Exception:
            continue
        hit = match_tool_obj(obj, allowed)
        if hit and json.dumps(hit) not in seen:
            seen.add(json.dumps(hit))
            found.append(hit)
    return found


def parse_text_tool(text: str) -> tuple[str, dict] | None:
    """First match only (kept for compat)."""
    hits = parse_text_tools(text)
    return hits[0] if hits else None


def synthetic_call_id(index: int, buf: dict) -> str:
    """Deterministic, turn-local, collision-resistant id for an id-less call.

    Stable for the same (index, tool, args) so a retried turn reuses the id;
    distinct across turns because the args differ.

    `buf["args"]` is a raw JSON **string** at call time, not a parsed dict. An
    earlier version of this function assumed a dict, so every id-less provider
    hit an AttributeError and silently degraded to the non-streaming fallback —
    the fix looked present but did nothing. Accept both shapes (#310).
    """
    import hashlib

    args = buf.get("args")
    if isinstance(args, dict):
        args_repr = repr(sorted(args.items()))
    else:
        args_repr = str(args or "")
    seed = f"{index}|{buf.get('name', '')}|{args_repr}"
    digest = hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:16]
    return f"call_{index}_{digest}"


def strip_control_leak(text: str) -> str:
    """Remove loop-machinery control notes echoed by the model. Never raises.

    Covers the tag plus the exact instruction sentences built with it: a
    small model repeats context verbatim, and a control sentence in the
    answer reads as product behavior ("I'll create… [sidekick-control]…").
    Intentional user-facing errors (residue failure, exhaustion recap,
    denials) never contain these fragments and pass through untouched.
    """
    try:
        if not isinstance(text, str) or not text:
            return text
        out = text.replace(CONTROL_TAG, "")
        out = out.replace(TEXT_ONLY_NOTICE, "")
        out = out.replace(CONTINUE_NOW, "")
        out = out.replace(CONTINUE_TOOLS, "")
        return out
    except Exception:
        return text


def strip_recap_leak(text: str) -> str:
    """Drop recap scaffolding the model echoed back. Never raises.

    Applied on top of `strip_control_leak` for the exhaustion path only, so an
    ordinary answer that happens to contain these words is untouched (they are
    distinctive harness strings, not user vocabulary).
    """
    try:
        if not isinstance(text, str) or not text:
            return text
        out = text
        for frag in RECAP_LEAKS:
            out = out.replace(frag, "")
        return out
    except Exception:
        return text


def prompt_echo(text: str) -> bool:
    """True when the model's 'answer' is really the harness prompt coming back.

    A model that never called a tool often echoes the recap instruction instead
    of answering (observed 2026-10-11). Anything containing the scaffolding, or
    an unfilled `<one line>` placeholder, is the prompt, not an explanation, so
    the deterministic report stands on its own.
    """
    try:
        t = text or ""
        if any(frag in t for frag in RECAP_LEAKS):
            return True
        if "<one line>" in t:
            return True
        # An unfilled template line is a prompt echo.
        if re.search(r"(?mi)^\s*(accomplished|blocked|next)\s*:\s*$", t):
            return True
        # A *filled* recap line is still the model asserting work it cannot
        # back. With an empty ledger that is exactly the fabrication this whole
        # path exists to prevent, so treat it as an echo and do not quote it.
        return bool(re.search(r"(?mi)^\s*(accomplished|blocked|next)\s*:\s*\S", t))
    except Exception:
        return True  # a probe that cannot decide must not become user-visible text
