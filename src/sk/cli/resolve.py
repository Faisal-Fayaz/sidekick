"""CLI model alias resolver: fast/smart/auto (split from sk/cli.py, pure move)."""

from __future__ import annotations

from .base import console


def _resolve_model(cfg, model_opt: str, task: str = "", quiet: bool = False) -> str:
    """--model > SIDEKICK_MODEL > config. Supports fast/smart/auto aliases.

    fast/smart resolve per provider (ollama: llama3.2:3b / qwen2.5-coder:7b).
    auto = router picks a tier from task text (sk run default).
    quiet skips the router print (machine-readable callers need clean stdout).
    """
    if model_opt:
        from sk.config import provider_tier, resolve_alias

        m = model_opt.strip()
        if m in ("fast", "smart"):
            return resolve_alias(cfg.provider, m, cfg.model)
        if m == "auto":
            from sk.router import pick_tier

            tier, reason = pick_tier(task)
            if cfg.provider in ("ollama", "lmstudio", "custom"):
                from sk.router import FAST_MODEL, SMART_MODEL

                picked = SMART_MODEL if tier == "smart" else FAST_MODEL
                if not quiet:
                    console.print(f"[dim]router → {picked} ({reason})[/dim]")
                return picked
            resolved = provider_tier(cfg.provider, tier, cfg.model)
            if not quiet:
                console.print(f"[dim]router → {resolved} ({reason})[/dim]")
            return resolved
        return m
    return cfg.model
