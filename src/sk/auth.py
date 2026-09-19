"""Provider auth + model listing. Shared by CLI, slash, wizard.

Rule: keys live in config file (chmod 600) or env, and are ALWAYS masked in
output. Keys never enter prompt context.
"""

from __future__ import annotations


def fetch_models(provider: str, base_url: str, api_key: str, timeout: int = 15) -> list[str]:
    """Live model ids. Ollama/LM Studio via /api/tags, clouds via /v1/models."""
    import httpx

    base = (base_url or "").rstrip("/")
    if not base:
        raise RuntimeError("no base URL (set provider first)")
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
