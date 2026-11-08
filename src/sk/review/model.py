"""One-shot structured model calls for review.

Review does not need the full agent loop: no tools, no file writes, one prompt
per diff chunk. This module keeps the provider boundary small and testable.

OpenAI-compatible providers use the same ``get_client`` as the agent. Anthropic
uses the existing Messages backend. Both are called with temperature 0 and a
strict "JSON only" instruction; parsing is tolerant of fences/prose because
small local models are not reliable formatters.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any


class ModelError(RuntimeError):
    """The model call failed. Callers should record it and continue."""


def complete(
    cfg: Any,
    *,
    system: str,
    user: str,
    max_tokens: int = 4096,
    timeout: float = 300.0,
) -> str:
    """Return one assistant message. Raises ModelError on failure."""
    provider = str(getattr(cfg, "provider", "") or "").lower()
    if provider == "anthropic":
        return _complete_anthropic(
            cfg, system=system, user=user, max_tokens=max_tokens, timeout=timeout
        )
    return _complete_openai(cfg, system=system, user=user, max_tokens=max_tokens, timeout=timeout)


def _complete_openai(cfg: Any, *, system: str, user: str, max_tokens: int, timeout: float) -> str:
    try:
        from openai import OpenAI

        client = OpenAI(
            base_url=str(getattr(cfg, "effective_base_url")()),
            api_key=str(getattr(cfg, "effective_api_key")()),
            timeout=timeout,
        )
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = client.chat.completions.create(
                    model=str(getattr(cfg, "model", "")),
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.0,
                    max_tokens=max_tokens,
                )
                return str(r.choices[0].message.content or "")
            except Exception as e:  # noqa: BLE001 - provider errors are opaque
                last = e
                status = getattr(e, "status_code", None)
                if status in (429, 529) and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                break
        raise ModelError(f"model call failed: {last}")
    except ModelError:
        raise
    except Exception as e:  # noqa: BLE001 - missing dependency/config
        raise ModelError(f"model call failed: {e}") from e


def _complete_anthropic(
    cfg: Any, *, system: str, user: str, max_tokens: int, timeout: float
) -> str:
    try:
        from ..anthropic_backend import _post

        data = _post(
            str(getattr(cfg, "effective_base_url")()),
            str(getattr(cfg, "effective_api_key")()),
            {
                "model": str(getattr(cfg, "model", "")),
                "max_tokens": max_tokens,
                "temperature": 0,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=timeout,
        )
        blocks = data.get("content") or []
        text = "".join(
            str(b.get("text") or "")
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        )
        if not text:
            raise ModelError("model returned no text")
        return text
    except ModelError:
        raise
    except Exception as e:  # noqa: BLE001 - provider errors are opaque
        raise ModelError(f"model call failed: {e}") from e


_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def _candidates(text: str) -> list[str]:
    out: list[str] = []
    for m in _FENCE.finditer(text or ""):
        out.append(m.group(1).strip())
    out.append((text or "").strip())
    return out


def parse_json(text: str) -> Any:
    """Best-effort parse of the first JSON value in a model response."""
    decoder = json.JSONDecoder()
    for candidate in _candidates(text):
        for i, ch in enumerate(candidate):
            if ch not in "[{":
                continue
            try:
                obj, _ = decoder.raw_decode(candidate[i:])
                return obj
            except Exception:
                continue
    return None


def parse_findings(text: str) -> list[dict[str, Any]]:
    obj = parse_json(text)
    if isinstance(obj, dict):
        for key in ("findings", "results", "issues"):
            if isinstance(obj.get(key), list):
                return [x for x in obj[key] if isinstance(x, dict)]
        # A single finding object is accepted too.
        if {"file", "line", "problem"} <= set(obj):
            return [obj]
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    return []


def parse_assessment(text: str) -> dict[str, Any] | None:
    obj = parse_json(text)
    if isinstance(obj, dict):
        if isinstance(obj.get("assessment"), dict):
            return obj["assessment"]
        if "verdict" in obj:
            return obj
    return None
