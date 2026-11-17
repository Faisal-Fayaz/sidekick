"""CLI command: sk bundle — build, verify and use an air-gapped bundle.

```bash
sk bundle build --out /media/bundle
sk bundle verify --in /media/bundle
sk bundle install --in /media/bundle --into /opt/sidekick
```
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..base import console

bundle_app = typer.Typer(help="Build, verify and install an offline bundle")


@bundle_app.command("build")
def build(
    out: Path = typer.Option(..., "--out", help="Directory to write the bundle into"),
):
    """Build a verified bundle for this machine (OS, arch, Python version)."""
    from sk.bundle import build_bundle

    try:
        bundle = build_bundle(out)
    except Exception as e:  # noqa: BLE001 - a build failure is a report
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e
    from sk.bundle import read_manifest

    manifest = read_manifest(bundle)
    size_mb = int(manifest.get("size", 0)) / 1_000_000
    console.print(
        f"[green]bundle built at {bundle}[/green] — "
        f"version {manifest.get('version')}, {len(manifest.get('files') or [])} files, "
        f"{size_mb:.1f} MB"
    )
    console.print(
        "[dim]Model weights are not included; see docs/sdk.md for the offline turn.[/dim]"
    )


@bundle_app.command("verify")
def verify(
    bundle: Path = typer.Option(..., "--in", help="The bundle directory"),
):
    """Verify every file in a bundle against its manifest."""
    from sk.bundle import verify_bundle

    ok, problems = verify_bundle(bundle)
    if not ok:
        for problem in problems:
            console.print(f"[red]{problem}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]bundle OK at {bundle}[/green]")


@bundle_app.command("install")
def install(
    bundle: Path = typer.Option(..., "--in", help="The bundle directory"),
    target: Path = typer.Option(..., "--into", help="Where to create the virtual environment"),
):
    """Verify a bundle and install it without touching any package index."""
    from sk.bundle import install_bundle

    ok, message = install_bundle(bundle, target)
    if not ok:
        console.print(f"[red]{message}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]{message}[/green]")
    console.print(f"[dim]check it: {target}/bin/sk --version[/dim]")
