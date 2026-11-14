"""CLI command: sk serve — a localhost HTTP surface over the agent.

Localhost only, and always behind a token: a service that can run the agent
must not be reachable from the network, and a page in the user's browser must
not be able to drive it.

```bash
sk serve                 # 127.0.0.1:8787, token printed and written to ~/.sidekick
sk serve --port 9000
sk serve --read-only     # the agent may read, but not write
```
"""

from __future__ import annotations

import typer

from ..base import app, console


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Loopback address to bind"),
    port: int = typer.Option(8787, "--port", help="Port to bind"),
    read_only: bool = typer.Option(False, "--read-only", help="The agent may read, but not write"),
):
    """Serve the agent over HTTP, on this machine only.

    The binding is refused for anything that is not a loopback address, and
    every request needs the token that is printed at startup.
    """
    from ..serve import generate_token, is_loopback, serve, write_token

    if not is_loopback(host):
        console.print(
            f"[red]refusing to bind {host}[/red] The local API serves this machine only, "
            "never 0.0.0.0."
        )
        raise typer.Exit(2)

    token = generate_token()
    path = write_token(token)
    console.print(f"[dim]token written to {path}[/dim]")
    console.print(f"[green]token:[/green] {token}")
    console.print(f"[dim]serving on http://{host}:{port} — Ctrl+C to stop[/dim]")
    if read_only:
        console.print("[dim]read-only: writes are denied[/dim]")

    try:
        serve(host=host, port=port, token=token, read_only=read_only)
    except OSError as e:
        console.print(f"[red]could not bind {host}:{port} — {e}[/red]")
        raise typer.Exit(1) from e
