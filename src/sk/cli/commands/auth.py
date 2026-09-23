"""CLI commands: auth sub-app + provider/key/model-pick flow helpers (split from sk/cli.py, pure move)."""

from __future__ import annotations

import typer

from sk.config import Config

from ..base import _cfg, app, console

auth_app = typer.Typer(help="Keys: sk auth add/list/status/remove (keys masked, validated live)")

app.add_typer(auth_app, name="auth")


def _pick_provider(default: str = "", *, force_list: bool = False) -> str:
    from sk.config import PRESETS

    names = list(PRESETS)
    if default and default in names and not force_list:
        return default
    console.print("Provider:")
    for i, n in enumerate(names, 1):
        console.print(f"  {i}. {n}")
    while True:
        try:
            raw = (
                console.input("Pick [1-{}] ({}): ".format(len(names), default or "ollama")).strip()
                or default
                or "ollama"
            )
        except (EOFError, KeyboardInterrupt):
            raise typer.Exit(1)
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return names[int(raw) - 1]
        if raw.lower() in names:
            return raw.lower()
        console.print("[red]not in list, try again[/red]")


def _ask_key() -> str:
    try:
        return (typer.prompt("API key (hidden)", hide_input=True) or "").strip()
    except (EOFError, KeyboardInterrupt):
        raise typer.Exit(1)


@auth_app.command("add")
def auth_add(
    provider: str = typer.Argument("", help="Provider, omit for picker"),
    key: str = typer.Option("", help="Key inline (hidden prompt if omitted)"),
):
    """Add a key: sk auth add groq (validates live before saving)."""
    from sk.auth import validate_key
    from sk.config import PRESETS

    cfg = _cfg()
    p = provider.strip().lower() or _pick_provider(cfg.provider, force_list=True)
    if p not in PRESETS:
        console.print(f"[red]unknown provider. Pick: {', '.join(PRESETS)}[/red]")
        raise typer.Exit(1)
    base = (
        PRESETS[p]["base_url"]
        if p != cfg.provider or not cfg.base_url
        else cfg.effective_base_url()
    )
    k = key.strip() or _ask_key()
    if p in ("ollama", "lmstudio"):
        cfg.provider, cfg.model, cfg.base_url, cfg.api_key = (
            p,
            PRESETS[p]["model"] or cfg.model,
            "",
            "",
        )
        cfg.save()
        console.print(f"[green]provider set to {p}, no key needed locally.[/green]")
        return
    ok, msg = validate_key(p, base, k)
    console.print(f"[green]✓ {msg}[/green]" if ok else f"[red]✗ {msg}[/red]")
    if not ok:
        raise typer.Exit(1)
    cfg.provider, cfg.base_url, cfg.api_key = p, "", k
    if not cfg.model or cfg.model in (PRESETS.get(cfg.provider, {}).get("model", ""),):
        cfg.model = PRESETS[p]["model"]
    cfg.save()
    console.print(f"[green]saved. Model is {cfg.model} — change with `sk model`.[/green]")


@auth_app.command("list")
def auth_list():
    """Show providers + masked key state."""
    from sk.config import PRESETS

    cfg = _cfg()
    for n in PRESETS:
        cur = "← current" if n == cfg.provider else ""
        key = Config.mask(cfg.effective_api_key()) if n == cfg.provider else "—"
        console.print(f"• [cyan]{n}[/cyan] key={key} {cur}")


@auth_app.command("status")
def auth_status(provider: str = typer.Argument("", help="Provider, omit for current")):
    """Validate reachability + key for a provider."""
    from sk.auth import provider_status

    cfg = _cfg()
    if provider.strip():
        from sk.config import PRESETS

        p = provider.strip().lower()
        if p not in PRESETS:
            console.print("[red]unknown provider[/red]")
            raise typer.Exit(1)
        import copy

        cfg = copy.copy(cfg)
        cfg.provider = p
        if p != _cfg().provider:
            cfg.base_url, cfg.api_key = "", ""
    ok, msg = provider_status(cfg)
    console.print(
        f"[green]✓ {cfg.provider}: {msg}[/green]" if ok else f"[red]✗ {cfg.provider}: {msg}[/red]"
    )
    if not ok:
        raise typer.Exit(1)


@auth_app.command("remove")
def auth_remove(provider: str = typer.Argument("", help="Provider, omit for current")):
    """Forget a key (and reset model default)."""
    from sk.config import PRESETS

    cfg = _cfg()
    p = provider.strip().lower() or cfg.provider
    if p not in PRESETS:
        console.print("[red]unknown provider[/red]")
        raise typer.Exit(1)
    if p == cfg.provider:
        cfg.api_key, cfg.base_url, cfg.model = "", "", PRESETS[p]["model"]
        cfg.save()
    console.print(f"[yellow]forgot {p}.[/yellow]")


def _pick_model_name(p: str, names: list[str], cfg) -> str:
    """Numbered curated list, preset default first + Enter-to-accept."""
    from sk.config import PRESETS

    if not names:
        console.print("[yellow]empty list.[/yellow]")
        raise typer.Exit(1)
    default = PRESETS.get(p, {}).get("model", "")
    ordered = ([default] if default in names else []) + [n for n in names if n != default]
    shown = ordered[:10]
    console.print(f"Models on {p}:")
    for i, n in enumerate(shown, 1):
        tags = []
        if n == default:
            tags.append("recommended")
        if n == cfg.model and p == cfg.provider:
            tags.append("current")
        tag = f" ({', '.join(tags)})" if tags else ""
        console.print(f"  {i}. {n}{tag}")
    if len(ordered) > len(shown):
        console.print(f"  [dim]...{len(ordered) - len(shown)} more — or type any id[/dim]")
    while True:
        try:
            raw = console.input(f"Pick [1-{len(shown)}, Enter={shown[0]}] or id: ").strip() or "1"
        except (EOFError, KeyboardInterrupt):
            raise typer.Exit(1)
        if raw.isdigit() and 1 <= int(raw) <= len(shown):
            return shown[int(raw) - 1]
        if raw and not raw.isdigit():
            return raw
        console.print("[red]empty, try again[/red]")


def _connect_flow() -> Config:
    """Shared provider → key → validate → model flow. Returns saved cfg."""
    from sk.auth import validate_key
    from sk.config import PRESETS

    cfg = _cfg()
    p = _pick_provider(cfg.provider, force_list=True)
    base = PRESETS[p]["base_url"]
    if p in ("ollama", "lmstudio"):
        cfg.provider, cfg.model, cfg.base_url, cfg.api_key = (
            p,
            PRESETS[p]["model"] or cfg.model,
            "",
            "",
        )
        cfg.save()
        console.print(f"[green]provider set to {p}, no key needed locally.[/green]")
    else:
        k = _ask_key()
        ok, msg = validate_key(p, base, k)
        console.print(f"[green]✓ {msg}[/green]" if ok else f"[red]✗ {msg}[/red]")
        if not ok:
            raise typer.Exit(1)
        cfg.provider, cfg.base_url, cfg.api_key = p, "", k
        cfg.model = PRESETS[p]["model"]
        cfg.save()
        console.print("[green]key saved (chmod 600).[/green]")
    from sk.auth import chat_models, fetch_models

    try:
        names = chat_models(fetch_models(p, base, cfg.effective_api_key()))
    except Exception as e:
        console.print(f"[red]cannot list models: {e}[/red]")
        return cfg
    if names:
        cfg.model = _pick_model_name(p, names, cfg)
        cfg.save()
    return cfg
