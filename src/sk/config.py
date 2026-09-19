"""Config loader for sidekick. Reads ~/.sidekick/config.toml, creates defaults if missing."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
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

DEFAULTS = {
    "model": "qwen2.5-coder:7b",
    "base_url": "http://localhost:11434/v1",
    "api_key": "ollama",
    "max_steps": 5,
    "temperature": 0.2,
}


@dataclass
class Config:
    model: str = DEFAULTS["model"]
    base_url: str = DEFAULTS["base_url"]
    api_key: str = DEFAULTS["api_key"]
    max_steps: int = DEFAULTS["max_steps"]
    temperature: float = DEFAULTS["temperature"]

    @classmethod
    def load(cls) -> "Config":
        # env overrides win (useful for cloud fallback later)
        model = os.getenv("SIDEKICK_MODEL", "")
        base_url = os.getenv("SIDEKICK_BASE_URL", "")
        api_key = os.getenv("SIDEKICK_API_KEY", "")

        file_vals: dict = {}
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "rb") as f:
                    file_vals = tomllib.load(f)
            except Exception:
                file_vals = {}

        def pick(key: str) -> str | int | float:
            if key == "model" and model:
                return model
            if key == "base_url" and base_url:
                return base_url
            if key == "api_key" and api_key:
                return api_key
            return file_vals.get(key, DEFAULTS[key])

        return cls(
            model=str(pick("model")),
            base_url=str(pick("base_url")),
            api_key=str(pick("api_key")),
            max_steps=int(pick("max_steps")),
            temperature=float(pick("temperature")),
        )

    def ensure_created(self) -> Path:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            self.save()
        return CONFIG_PATH

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
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
            # minimal manual writer, no dependency needed
            lines = [f'{k} = "{v}"' if isinstance(v, str) else f"{k} = {v}" for k, v in data.items()]
            CONFIG_PATH.write_text("\n".join(lines) + "\n")
