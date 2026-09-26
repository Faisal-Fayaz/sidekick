"""Native Anthropic Messages API backend (token-streaming).

Used when cfg.provider == "anthropic". The rest of the agent speaks OpenAI
chat-completions, so this module translates at the boundary:

- tools_schema() (OpenAI functions) -> Anthropic tools [{name, description, input_schema}]
- OpenAI messages (system/user/assistant/tool roles) -> (system, messages)
  with strict role alternation (consecutive same-role merged)
- tool_use blocks -> existing _run_tools_batch (approval + audit preserved)

Freshness: token streaming like the OpenAI path — content deltas go to
on_token as they arrive, thinking deltas to on_reasoning (falling back to
on_token, same contract as agent._stream_chat). Non-streaming _post remains
for summaries and as a fallback when streaming fails.

No new deps (httpx already required). Fully offline except the API calls.
"""

from __future__ import annotations

from collections.abc import Iterator

from .auth import anthropic_headers


def openai_tools_to_anthropic(openai_schema: list[dict]) -> list[dict]:
    """Convert OpenAI function schemas to Anthropic tool definitions."""
    out: list[dict] = []
    for entry in openai_schema or []:
        fn = entry.get("function", entry) if isinstance(entry, dict) else {}
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        params = fn.get("parameters") or {"type": "object", "properties": {}}
        out.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": params,
            }
        )
    return out


def _blocks(content) -> list[dict]:
    """Normalize message content to Anthropic content blocks."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        blocks: list[dict] = []
        for part in content:
            if isinstance(part, str):
                if part:
                    blocks.append({"type": "text", "text": part})
            elif isinstance(part, dict):
                if part.get("type") in ("text", "image"):
                    blocks.append(part)
                elif "text" in part:
                    blocks.append({"type": "text", "text": str(part["text"])})
        return blocks
    return [{"type": "text", "text": str(content)}]


def openai_messages_to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """Split system prompt out; enforce alternating user/assistant roles.

    - system role -> joined system string (Anthropic takes it as a param)
    - assistant tool_calls -> tool_use blocks appended after any text
    - tool role -> user message carrying tool_result blocks
    - consecutive same-role messages merged (Anthropic rejects repeats)
    - leading non-user messages dropped (Anthropic requires user-first)
    """
    import json

    system_parts: list[str] = []
    converted: list[tuple[str, list[dict]]] = []
    auto_n = 0
    for m in messages or []:
        role = m.get("role", "user")
        if role == "system":
            if m.get("content"):
                system_parts.append(str(m["content"]))
            continue
        if role == "assistant" and m.get("tool_calls"):
            blocks = _blocks(m.get("content"))
            for tc in m["tool_calls"] or []:
                fn = (tc.get("function", {}) or {}) if isinstance(tc, dict) else {}
                tc_id = tc.get("id", f"call_{auto_n}") if isinstance(tc, dict) else f"call_{auto_n}"
                auto_n += 1
                name = fn.get("name", "") if isinstance(fn, dict) else ""
                raw_args = (fn.get("arguments", {}) or {}) if isinstance(fn, dict) else {}
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except Exception:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc_id, "name": name, "input": args})
            converted.append(("assistant", blocks or [{"type": "text", "text": ""}]))
        elif role == "tool":
            tc_id = m.get("tool_call_id", f"call_{auto_n}")
            auto_n += 1
            converted.append(
                (
                    "user",
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": tc_id,
                            "content": str(m.get("content", "")),
                        }
                    ],
                )
            )
        elif role == "assistant":
            converted.append(
                ("assistant", _blocks(m.get("content")) or [{"type": "text", "text": ""}])
            )
        else:
            converted.append(("user", _blocks(m.get("content")) or [{"type": "text", "text": ""}]))
    # merge consecutive same-role + drop leading non-user
    merged: list[tuple[str, list[dict]]] = []
    for role, blocks in converted:
        if merged and merged[-1][0] == role:
            merged[-1][1].extend(blocks)
        else:
            merged.append((role, blocks))
    while merged and merged[0][0] != "user":
        merged.pop(0)
    return ("\n\n".join(system_parts), [{"role": r, "content": b} for r, b in merged])


def _cache_breakpoints(system: str, tools: list[dict]) -> tuple[str | list[dict], list[dict]]:
    """Attach prompt-caching breakpoints: system block + end of tools definition.

    System + tools are static within a session, so Anthropic serves repeats
    from cache (up to 10x cheaper). Returns "" for blank system (omit it).
    Minimum cacheable length (~1k tokens) means tiny prompts simply never
    form a cache entry — harmless. OpenAI-compatible providers cache matching
    prefixes automatically server-side, so this backend is the only place
    markers are needed.
    """
    sys_payload: str | list[dict] = ""
    if system.strip():
        sys_payload = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    if tools:
        tools = [dict(t) for t in tools]
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return (sys_payload, tools)


def _post(base_url: str, api_key: str, payload: dict, timeout: float = 300.0) -> dict:
    """POST /v1/messages. Honors Retry-After on 429/529 (2 retries). Returns decoded body."""
    import time

    import httpx

    url = f"{(base_url or '').rstrip('/')}/v1/messages"
    last_err = "unknown error"
    for attempt in range(3):
        try:
            r = httpx.post(url, headers=anthropic_headers(api_key), json=payload, timeout=timeout)
            if r.status_code in (429, 529) and attempt < 2:
                wait = 5
                try:
                    wait = max(1, min(30, int(float(r.headers.get("retry-after", 5)))))
                except Exception:
                    pass
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                try:
                    err = r.json().get("error", {})
                    last_err = f"HTTP {r.status_code}: {err.get('message', r.text[:200])}"
                except Exception:
                    last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                r.raise_for_status()
            return r.json()
        except Exception as e:
            if "HTTP " in str(e) or attempt >= 2:
                raise RuntimeError(last_err if "HTTP " in last_err else str(e)[:300]) from e
            last_err = str(e)[:300]
            time.sleep(5)
    raise RuntimeError(last_err)


def _iter_sse_lines(response) -> Iterator[tuple[str, str]]:
    """Yield (event, data) pairs from an SSE byte stream. Tolerates [DONE]."""
    event: str = "message"
    data_lines: list[str] = []
    for line in response.iter_lines():
        if isinstance(line, bytes):
            try:
                line = line.decode("utf-8", "replace")
            except Exception:
                line = ""
        line = (line or "").strip()
        if not line:
            if data_lines:
                yield (event, "\n".join(data_lines))
            event, data_lines = "message", []
            continue
        if line.startswith(":"):
            continue  # heartbeat comment
        if line.startswith("event:"):
            event = line[6:].strip() or event
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if data_lines:
        yield (event, "\n".join(data_lines))


def _stream(
    base_url: str,
    api_key: str,
    payload: dict,
    on_token=None,
    on_reasoning=None,
    timeout: float = 300.0,
) -> tuple[list[dict], str]:
    """POST /v1/messages with stream:true. Returns (content blocks, stop_reason).

    Content deltas stream to on_token as they arrive; thinking deltas to
    on_reasoning (falling back to on_token, same as agent._stream_chat).
    Tool input_json deltas accumulate silently. Raises RuntimeError on
    transport/API errors — callers fall back to _post.
    """
    import json

    import httpx

    def _emit(fn, chunk: str) -> None:
        if fn is None or not chunk:
            return
        try:
            fn(chunk)
        except Exception:
            pass

    url = f"{(base_url or '').rstrip('/')}/v1/messages"
    body = dict(payload or {})
    body["stream"] = True
    on_r = on_reasoning if on_reasoning is not None else on_token
    acc: dict[int, dict] = {}
    order: list[int] = []
    stop_reason = ""
    try:
        with httpx.stream(
            "POST", url, headers=anthropic_headers(api_key), json=body, timeout=timeout
        ) as r:
            if r.status_code >= 400:
                try:
                    raw = r.read()
                    err = json.loads(raw.decode("utf-8", "replace")).get("error", {})
                    msg = f"HTTP {r.status_code}: {err.get('message', str(err)[:200])}"
                except Exception:
                    msg = f"HTTP {r.status_code}"
                raise RuntimeError(msg)
            for event, data in _iter_sse_lines(r):
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                if event == "error" or obj.get("type") == "error":
                    err = obj.get("error", obj)
                    raise RuntimeError(
                        f"stream error: {err.get('message', str(err)[:200]) if isinstance(err, dict) else str(err)[:200]}"
                    )
                kind = obj.get("type", "")
                if kind == "content_block_start":
                    idx = int(obj.get("index", 0))
                    block = obj.get("content_block", {}) or {}
                    acc[idx] = {
                        "type": block.get("type", "text"),
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "text": block.get("text", ""),
                        "thinking": "",
                        "input_json": "",
                    }
                    if idx not in order:
                        order.append(idx)
                elif kind == "content_block_delta":
                    idx = int(obj.get("index", 0))
                    blk = acc.setdefault(
                        idx,
                        {
                            "type": "text",
                            "id": "",
                            "name": "",
                            "text": "",
                            "thinking": "",
                            "input_json": "",
                        },
                    )
                    if idx not in order:
                        order.append(idx)
                    delta = obj.get("delta", {}) or {}
                    dtype = delta.get("type", "")
                    if dtype == "text_delta":
                        chunk = delta.get("text", "")
                        blk["text"] += chunk
                        _emit(on_token, chunk)
                    elif dtype == "thinking_delta":
                        chunk = delta.get("thinking", "")
                        blk["thinking"] += chunk
                        blk["type"] = "thinking"
                        _emit(on_r, chunk)
                    elif dtype == "input_json_delta":
                        blk["input_json"] += delta.get("partial_json", "")
                    # signature_delta and friends: carried opaquely, never displayed
                elif kind == "message_delta":
                    stop_reason = (
                        (obj.get("delta", {}) or {}).get("stop_reason", "")
                        or obj.get("stop_reason", "")
                        or stop_reason
                    )
                elif kind == "message_stop":
                    break
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(str(e)[:300]) from e
    blocks: list[dict] = []
    for idx in order:
        blk = acc[idx]
        btype = blk.get("type", "text")
        if btype == "thinking":
            if blk.get("thinking"):
                blocks.append({"type": "thinking", "thinking": blk["thinking"]})
        elif btype == "tool_use":
            try:
                parsed = json.loads(blk.get("input_json", "") or "{}")
                if not isinstance(parsed, dict):
                    parsed = {}
            except Exception:
                parsed = {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": blk.get("id", ""),
                    "name": blk.get("name", ""),
                    "input": parsed,
                }
            )
        else:
            if blk.get("text"):
                blocks.append({"type": "text", "text": blk["text"]})
    return (blocks, stop_reason)


def run_anthropic_agent(
    user_msg: str,
    history: list[dict],
    cfg,
    on_tool=None,
    on_token=None,
    approve=None,
    on_reasoning=None,
    auto_approve: bool = False,
    session: str = "",
    review_plan=None,
) -> str:
    """One agent turn over the native Messages API. Same contract as run_agent."""
    from .agent import (
        _SUMMARY_PROMPT,
        _maybe_review_plan,
        _provider_host,
        _spend_blocked,
        build_messages,
        prepare_history,
    )
    from .store import log_tool_run
    from .tools import tools_schema

    session = session or ""
    blocked = _spend_blocked(session, cfg)
    if blocked is not None:
        if on_token is not None:
            try:
                on_token(blocked)
            except Exception:
                pass
        return blocked
    try:
        log_tool_run(
            session,
            "llm_call",
            cfg.model,
            approved=True,
            provider=cfg.provider,
            host=_provider_host(cfg),
        )
    except Exception:
        pass

    def _summarize(text: str) -> str:
        resp = _post(
            cfg.effective_base_url(),
            cfg.effective_api_key(),
            {
                "model": cfg.model,
                "max_tokens": 400,
                "messages": [{"role": "user", "content": _SUMMARY_PROMPT + "\n\n" + text[:6000]}],
            },
        )
        out = "".join(
            b.get("text", "")
            for b in (resp.get("content", []) if isinstance(resp, dict) else [])
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
        if not out:
            raise RuntimeError("empty summary")
        return out

    history = prepare_history(session, history, cfg, _summarize)
    system, messages = openai_messages_to_anthropic(
        build_messages(user_msg, history, cfg, auto_approve)
    )
    tools = openai_tools_to_anthropic(tools_schema())
    system_payload, tools = _cache_breakpoints(system, tools)
    max_tokens = 800
    seen: dict[str, str] = {}
    final_text = ""

    for _ in range(max(1, cfg.max_steps)):
        payload: dict = {
            "model": cfg.model,
            "max_tokens": max_tokens,
            "temperature": cfg.temperature,
            "messages": messages,
        }
        if system_payload:
            payload["system"] = system_payload
        if tools:
            payload["tools"] = tools
        try:
            blocks, _stop = _stream(
                cfg.effective_base_url(),
                cfg.effective_api_key(),
                payload,
                on_token,
                on_reasoning,
            )
            streamed = True
        except Exception:
            streamed = False
            try:
                resp = _post(cfg.effective_base_url(), cfg.effective_api_key(), payload)
            except Exception as e:
                return f"Error talking to anthropic ({cfg.effective_base_url()} model={cfg.model}): {e}"
            blocks = resp.get("content", []) if isinstance(resp, dict) else []
        texts = [
            b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
        ]
        uses = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
        text = "".join(texts).strip()
        if not streamed and text and on_token is not None:
            try:
                on_token(text)  # fallback path: deltas never flowed, emit whole text
            except Exception:
                pass
        if not uses:
            return text or "(empty)"
        batch = [
            (
                u.get("name", ""),
                u.get("input", {}) if isinstance(u.get("input"), dict) else {},
            )
            for u in uses
        ]
        from .agent import _run_tools_batch
        from .model_profiles import max_parallel_for

        proceed, turn_approve = _maybe_review_plan(
            batch, approve, review_plan, auto_approve, session, cfg.provider, _provider_host(cfg)
        )
        if not proceed:
            return "Plan denied by user — nothing was executed."
        # tool turn: append assistant tool_use + dispatch batch, then continue
        messages.append({"role": "assistant", "content": blocks})
        max_parallel = max_parallel_for(getattr(cfg, "model", ""))
        outs = _run_tools_batch(
            batch, turn_approve, on_tool, seen, session, cfg, max_workers=max_parallel
        )
        for u, (result, _) in zip(uses, outs):
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": u.get("id", ""),
                            "content": result[:6000],
                        }
                    ],
                }
            )
        final_text = text
    return final_text or "(max steps reached)"
