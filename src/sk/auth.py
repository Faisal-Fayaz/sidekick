"""Provider auth + model listing. Shared by CLI, slash, wizard.

Rule: keys live in config file (chmod 600) or env, and are ALWAYS masked in
output. Keys never enter prompt context.
"""

from __future__ import annotations

ANTHROPIC_VERSION = "2023-06-01"


def anthropic_headers(api_key: str) -> dict[str, str]:
    """Native Messages API headers. Key never leaves this dict."""
    return {
        "x-api-key": api_key or "",
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


def fetch_models(provider: str, base_url: str, api_key: str, timeout: int = 15) -> list[str]:
    """Live model ids. Ollama/LM Studio via /api/tags, clouds via /v1/models."""
    import httpx

    base = (base_url or "").rstrip("/")
    if not base:
        raise RuntimeError("no base URL (set provider first)")
    if provider == "anthropic":
        r = httpx.get(f"{base}/v1/models", headers=anthropic_headers(api_key), timeout=timeout)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if provider in ("ollama", "lmstudio"):
        r = httpx.get(f"{base.removesuffix('/v1')}/api/tags", timeout=min(timeout, 8))
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    r = httpx.get(f"{base}/models", headers=headers, timeout=timeout)
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", [])]


def validate_key(provider: str, base_url: str, api_key: str) -> tuple[bool, str]:
    """Check a key against the live API. Returns (ok, message)."""
    if provider not in ("ollama", "lmstudio") and not (api_key or "").strip():
        return (False, "empty key")
    try:
        names = fetch_models(provider, base_url, api_key)
    except Exception as e:
        msg = str(e)
        if "401" in msg or "403" in msg:
            return (False, "key rejected (401/403) — check it and retry")
        return (False, f"unreachable: {msg[:150]}")
    return (True, f"valid ({len(names)} models listed)")


def provider_status(cfg) -> tuple[bool, str]:
    """One-line health for doctor/status surfaces."""
    if cfg.provider not in ("ollama", "lmstudio") and not cfg.effective_api_key():
        return (False, "no API key — `sk auth add` or SIDEKICK_API_KEY")
    return validate_key(cfg.provider, cfg.effective_base_url(), cfg.effective_api_key())


NON_CHAT_HINTS = ("tts", "image", "transcribe", "live", "embedding", "vision-preview", "audio")


def chat_models(names: list[str]) -> list[str]:
    """Drop non-chat ids (tts/image/transcribe/live…), keep order, dedupe."""
    out: list[str] = []
    for n in names:
        low = n.lower()
        if any(h in low for h in NON_CHAT_HINTS):
            continue
        if n not in out:
            out.append(n)
    return out


def ping(
    provider: str, base_url: str, api_key: str, model: str, timeout: int = 30
) -> tuple[bool, str]:
    """One tiny completion to prove end-to-end works. No tools, no history."""
    if provider == "anthropic":
        return _ping_anthropic(base_url, api_key, model, timeout)
    from openai import OpenAI

    try:
        client = OpenAI(base_url=base_url.rstrip("/"), api_key=api_key, timeout=timeout)
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "say hi"}],
            max_tokens=5,
            stream=False,
        )
        text = ((r.choices[0].message.content) or "").strip()
        return (True, text[:100] or "(empty reply, but reachable)")
    except Exception as e:
        return (False, str(e)[:200])


def _ping_anthropic(base_url: str, api_key: str, model: str, timeout: int) -> tuple[bool, str]:
    """Same proof over the native Messages API (no SDK dep)."""
    import httpx

    try:
        r = httpx.post(
            f"{(base_url or '').rstrip('/')}/v1/messages",
            headers=anthropic_headers(api_key),
            json={
                "model": model,
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "say hi"}],
            },
            timeout=timeout,
        )
        r.raise_for_status()
        texts = [b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text"]
        text = "".join(texts).strip()
        return (True, text[:100] or "(empty reply, but reachable)")
    except Exception as e:
        return (False, str(e)[:200])
