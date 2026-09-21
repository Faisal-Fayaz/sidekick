"""Config loader for sidekick. Reads ~/.sidekick/config.toml, creates defaults if missing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # py3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

try:
    import tomli_w  # for writing; fallback to manual write
    _HAS_TOMLI_W = True
except ImportError:
    _HAS_TOMLI_W = False


CONFIG_DIR = Path.home() / ".sidekick"
CONFIG_PATH = CONFIG_DIR / "config.toml"

# OpenAI-compatible providers. Anything speaking /v1/chat/completions works,
# including local servers (ollama, LM Studio, llama.cpp --server).
PRESETS: dict[str, dict[str, str]] = {
    "ollama": {"base_url": "http://localhost:11434/v1", "key": "ollama", "model": "qwen2.5-coder:7b"},
    "openai": {"base_url": "https://api.openai.com/v1", "key": "", "model": "gpt-4o-mini"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key": "", "model": "openai/gpt-oss-20b"},
    "together": {"base_url": "https://api.together.xyz/v1", "key": "", "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo"},
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "key": "", "model": "deepseek-chat"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key": "", "model": "openai/gpt-4o-mini"},
    "google": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "key": "", "model": "models/gemini-3.6-flash"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "key": "", "model": "models/gemini-3.6-flash"},
    "lmstudio": {"base_url": "http://localhost:1234/v1", "key": "lm-studio", "model": "local-model"},
    "custom": {"base_url": "", "key": "", "model": ""},
}

# fast/smart tiers per provider. Only verified IDs here; unknown tiers fall
# back to the provider default so aliases never 404.
TIERS: dict[str, dict[str, str]] = {
    "ollama": {"fast": "llama3.2:3b", "smart": "qwen2.5-coder:7b"},
    "openai": {"fast": "gpt-4o-mini", "smart": "gpt-4o"},
    "groq": {"fast": "openai/gpt-oss-20b", "smart": "openai/gpt-oss-120b"},
    "deepseek": {"fast": "deepseek-chat", "smart": "deepseek-reasoner"},
    "google": {"fast": "models/gemini-3.6-flash", "smart": "models/gemini-3.8-flash"},
    "gemini": {"fast": "models/gemini-3.6-flash", "smart": "models/gemini-3.8-flash"},
}


def provider_tier(provider: str, tier: str, fallback: str) -> str:
    """Resolve fast/smart for a provider, falling back to the saved default."""
    return TIERS.get(provider, {}).get(tier, "") or fallback


def resolve_alias(provider: str, alias: str, fallback: str) -> str:
    """Shared fast/smart alias resolver for CLI + slash. Returns fallback for unknown."""
    m = (alias or "").strip()
    if m in ("fast", "smart"):
        return provider_tier(provider, m, fallback)
    return m or fallback

DEFAULTS: dict[str, str | int | float] = {
    "provider": "ollama",
    "model": str(PRESETS["ollama"]["model"]),
    "base_url": "",  # empty = preset default; set = override (or custom's URL)
    "api_key": "",
    "max_steps": 5,
    "temperature": 0.2,
}


@dataclass
class Config:
    provider: str = str(DEFAULTS["provider"])
    model: str = str(DEFAULTS["model"])
    base_url: str = str(DEFAULTS["base_url"])  # override; "" = preset default
    api_key: str = str(DEFAULTS["api_key"])
    max_steps: int = int(DEFAULTS["max_steps"])
    temperature: float = float(DEFAULTS["temperature"])

    def effective_base_url(self) -> str:
        if self.base_url.strip():
            return self.base_url.strip()
        preset = PRESETS.get(self.provider, PRESETS["custom"])
        return preset["base_url"]

    def effective_api_key(self) -> str:
        if self.api_key.strip():
            return self.api_key.strip()
        preset = PRESETS.get(self.provider, PRESETS["custom"])
        return preset["key"]

    @classmethod
    def load(cls) -> Config:
        provider = os.getenv("SIDEKICK_PROVIDER", "")
        model = os.getenv("SIDEKICK_MODEL", "")
        base_url = os.getenv("SIDEKICK_BASE_URL", "")
        api_key = os.getenv("SIDEKICK_API_KEY", "")

        file_vals: dict[str, object] = {}
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "rb") as f:
                    file_vals = tomllib.load(f)
            except Exception:
                file_vals = {}

        prov = str(provider or file_vals.get("provider", DEFAULTS["provider"])).strip().lower()
        if prov not in PRESETS:
            prov = "custom"
        return cls(
            provider=prov,
            model=str(model or file_vals.get("model", "") or PRESETS[prov]["model"] or DEFAULTS["model"]),
            base_url=str(base_url or file_vals.get("base_url", "")),
            api_key=str(api_key or file_vals.get("api_key", "")),
            max_steps=int(str(file_vals.get("max_steps", DEFAULTS["max_steps"]))),
            temperature=float(str(file_vals.get("temperature", DEFAULTS["temperature"]))),
        )

    def ensure_created(self) -> Path:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            self.save()
        return CONFIG_PATH

    def save(self) -> None:
        import os as _os

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "max_steps": self.max_steps,
            "temperature": self.temperature,
        }
        if _HAS_TOMLI_W:
            with open(CONFIG_PATH, "wb") as f:
                tomli_w.dump(data, f)
        else:
            # minimal manual writer, no dependency needed (bools lowercase: valid TOML)
            def _toml(v):
                if isinstance(v, bool):
                    return "true" if v else "false"
                if isinstance(v, str):
                    return f'"{v}"'
                return f"{v}"

            lines = [f"{k} = {_toml(v)}" for k, v in data.items()]
            CONFIG_PATH.write_text("\n".join(lines) + "\n")
        if self.api_key.strip():
            try:
                _os.chmod(CONFIG_PATH, 0o600)
            except Exception:
                pass

    @staticmethod
    def mask(key: str) -> str:
        key = (key or "").strip()
        if len(key) <= 8:
            return "****" if key else "(none)"
        return f"{key[:3]}…{key[-4:]}"
