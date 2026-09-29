"""Image generation via provider /images endpoints. Never raises.

Sends OpenAI-style `{model, prompt, size, n: 1}` and handles both `b64_json`
and `url` response variants. Providers without a known image model (or
without an images endpoint at all, e.g. Anthropic native) degrade to a clear
message instead of a traceback.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

IMAGE_SIZES = ("256x256", "512x512", "1024x1024", "1024x1792", "1792x1024")
IMAGE_MODELS = {
    "openai": "gpt-image-1",
    "together": "black-forest-labs/FLUX.1-schnell",
}
MAX_IMAGE_BYTES = 25_000_000

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
MAX_INPUT_BYTES = 10_000_000

# Substring hints for vision-capable models. Conservative default: unknown
# models are treated as text-only (a clear message beats a hallucinated
# description). Never raises.
KNOWN_VISION_HINTS = (
    "gpt-4o",
    "gpt-4.1",
    "o1",
    "o3",
    "claude",
    "sonnet",
    "haiku",
    "opus",
    "gemini",
    "gemma-3",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "llava",
    "vision",
    "pixtral",
    "llama-3.2-vision",
    "llama4",
    "mimo-vl",
    "internvl",
    "minicpm-v",
    "phi-4-multimodal",
    "molmo",
    "florence",
)


def default_image_dir() -> Path:
    from .config import CONFIG_DIR

    d = CONFIG_DIR / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate_image(cfg, prompt: str, size: str = "", model: str = "", out: str = "") -> str:
    """Generate one image, save it, return the saved path. Never raises."""
    try:
        prompt = (prompt or "").strip()
        if not prompt:
            return "Error: empty prompt."
        size = (size or "1024x1024").strip()
        if size not in IMAGE_SIZES:
            return f"Error: bad size {size!r} (pick: {', '.join(IMAGE_SIZES)})."
        provider = getattr(cfg, "provider", "")
        if provider == "anthropic":
            return (
                "Error: the Anthropic native API has no images endpoint — "
                "switch to an OpenAI-compatible provider (`/provider openai`) "
                "or pass --model explicitly."
            )
        model = (model or "").strip() or IMAGE_MODELS.get(provider, "")
        if not model:
            return (
                f"Error: no default image model for provider {provider!r} — "
                "pass --model ID explicitly."
            )
        base = cfg.effective_base_url()
        key = cfg.effective_api_key()
        if not (base or "").strip() or not (key or "").strip():
            return "Error: image generation needs an API key (`sk auth add`)."
        if out and out.strip():
            from .tools.write import _check_write_path

            checked = _check_write_path(out.strip())
            if isinstance(checked, str):
                return checked
            dest = checked
        else:
            dest = default_image_dir() / f"img-{int(time.time())}.png"
        import httpx

        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            r = httpx.post(
                f"{base.rstrip('/')}/images/generations",
                json={"model": model, "prompt": prompt, "size": size, "n": 1},
                headers=headers,
                timeout=180.0,
            )
        except Exception as e:
            return f"Error: image request failed: {e}"
        if r.status_code != 200:
            return (
                f"Error: image generation failed (HTTP {r.status_code}): "
                f"{r.text[:200]} — try --model ID."
            )
        try:
            datum = (r.json().get("data", []) or [{}])[0]
        except Exception:
            return "Error: unreadable image response."
        blob: bytes | None = None
        if datum.get("b64_json"):
            try:
                blob = base64.b64decode(datum["b64_json"])
            except Exception as e:
                return f"Error: bad image payload: {e}"
        elif datum.get("url"):
            try:
                g = httpx.get(str(datum["url"]), timeout=120.0)
                g.raise_for_status()
                blob = g.content
            except Exception as e:
                return f"Error: could not fetch image url: {e}"
        if not blob:
            return "Error: empty image response."
        if len(blob) > MAX_IMAGE_BYTES:
            return f"Error: image too large ({len(blob)} bytes > 25MB), refusing."
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
        except Exception as e:
            return f"Error: could not save image: {e}"
        return f"Saved image to {dest} ({len(blob)} bytes)"
    except Exception as e:
        return f"Error: image generation failed: {e}"


def is_image_path(path: str) -> bool:
    """True for supported image extensions. Never raises."""
    try:
        return Path(path).suffix.lower() in IMAGE_EXTS
    except Exception:
        return False


def vision_capable(provider: str, model: str) -> bool:
    """Heuristic: can this model take image parts? Conservative default False."""
    try:
        low = (model or "").strip().lower()
        return bool(low) and any(h in low for h in KNOWN_VISION_HINTS)
    except Exception:
        return False


def extract_image_refs(text: str) -> tuple[str, list[str]]:
    """Pull @image.ext refs out of chat text. Returns (cleaned, paths).

    Each @path with an image extension becomes `[attached image: name]` in
    the text; the file paths are returned for image-part encoding. Missing
    or oversize files stay inline as notes. Never raises.
    """
    import re

    found: list[str] = []

    def repl(m: re.Match) -> str:
        raw = m.group(1)
        if not is_image_path(raw):
            return m.group(0)
        try:
            p = Path(raw).expanduser()
            if not p.is_file():
                return f"[image not found: {raw}]"
            if p.stat().st_size > MAX_INPUT_BYTES:
                return f"[image too large (>10MB): {raw}]"
            found.append(str(p.resolve()))
            return f"\n[attached image: {p.name}]\n"
        except Exception as e:
            return f"[error reading image @{raw}: {e}]"

    try:
        cleaned = re.sub(r"@([\w\-.~/][\w\-./~]*)", repl, text or "")
    except Exception:
        return text or "", []
    return cleaned, found


def encode_image_data_url(path: str) -> str | None:
    """File bytes as a data: URL, or None (missing/oversize/unreadable)."""
    try:
        p = Path(path).expanduser()
        suffix = p.suffix.lower()
        media = IMAGE_MEDIA_TYPES.get(suffix)
        if media is None or not p.is_file() or p.stat().st_size > MAX_INPUT_BYTES:
            return None
        import base64

        return f"data:{media};base64," + base64.b64encode(p.read_bytes()).decode()
    except Exception:
        return None
