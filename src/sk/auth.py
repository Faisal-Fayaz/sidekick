"""Provider auth + model listing. Shared by CLI, slash, wizard.

Rule: keys live in OS keyring, config file (chmod 600) or env, and are
ALWAYS masked in output. Keys never enter prompt context.
"""

from __future__ import annotations

ANTHROPIC_VERSION = "2023-06-01"


def store_api_key(provider: str, key: str) -> str:
    """Persist a key, keyring-first. Returns 'keyring' | 'file'.

    Callers store nothing on disk when 'keyring' wins (clear any stale file
    key); on 'file' they save via Config as before. Never raises.
    """
    from . import keyring as _kr

    try:
        if _kr.set_key(provider, key):
            return "keyring"
    except Exception:
        pass
    return "file"


def forget_api_key(provider: str) -> None:
    """Remove a key from keyring AND file. Never raises."""
    from . import keyring as _kr

    try:
        _kr.delete_key(provider)
    except Exception:
        pass


def key_source(cfg) -> str:
    """Where the current provider's effective key comes from.

    env | file | keyring | preset (local dummy) | none. File wins over
    keyring (explicit local config); keyring saves clear stale file keys,
    so they only coincide after manual edits. Never raises.
    """
    import os

    from . import keyring as _kr

    try:
        if os.getenv("SIDEKICK_API_KEY", "").strip():
            return "env"
        if cfg.provider == "opencode" and os.getenv("OPENCODE_API_KEY", "").strip():
            return "env"
        if (cfg.api_key or "").strip():
            return "file"
        if _kr.get_key(cfg.provider):
            return "keyring"
        from .config import PRESETS

        if PRESETS.get(cfg.provider, {}).get("key"):
            return "preset"
    except Exception:
        pass
    return "none"


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
            return (False, f"key rejected by {provider} (401/403) — check it and retry")
        detail = _api_error_message(e)
        return (False, f"unreachable: {detail[:150]}")
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
        return (False, _api_error_message(e))


def _api_error_message(exc: Exception, fallback: str = "") -> str:
    """Extract a human-readable message from an httpx HTTP error body.

    Anthropic/compat APIs return {"error": {"message": ...}} — surfacing it
    beats the generic "Client error '400 ...'". Falls back gracefully.
    """
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            err = resp.json().get("error", {})
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])[:300]
            if isinstance(err, str) and err:
                return err[:300]
        except Exception:
            pass
        try:
            if getattr(resp, "text", ""):
                return str(resp.text)[:300]
        except Exception:
            pass
    return (fallback or str(exc))[:300]


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
        return (False, _api_error_message(e))
