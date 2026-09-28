"""CLI commands: MCP server over stdio."""

from __future__ import annotations

import typer

from ..base import _cfg, app, console


@app.command()
def mcp(
    allow_writes: bool = typer.Option(
        False, "--allow-writes", help="Allow shell/writes/delete (else denied)"
    ),
):
    """Serve the 18 sidekick tools over MCP stdio (for Claude Desktop, IDEs)."""
    from sk.mcp_server import serve_stdio

    _cfg()  # establish config + memory namespace; stdout stays protocol-clean
    if allow_writes:
        import sys

        print(
            "mcp: writes allowed — only expose to clients you trust.", file=sys.stderr, flush=True
        )
    raise typer.Exit(serve_stdio(allow_writes))


@app.command(name="mcp-servers")
def mcp_servers():
    """List configured MCP client servers with a live tool check."""
    from sk.mcp_client import get_client, load_servers

    servers = load_servers()
    if not servers:
        console.print(
            "[dim](no MCP servers — add [mcp_servers.<name>] to "
            "~/.sidekick/config.toml, see docs/mcp-client.md)[/dim]"
        )
        return
    for spec in servers:
        target = f"{spec['command']} {' '.join(spec['args'])}".strip()
        try:
            tools = get_client(spec).list_tools()
            names = ", ".join(t["name"] for t in tools[:8])
            console.print(
                f"[green]●[/green] [cyan]{spec['name']}[/cyan] [dim]{target}[/dim]"
                f" — {len(tools)} tools: {names}"
            )
        except Exception as e:
            console.print(f"[red]○[/red] [cyan]{spec['name']}[/cyan] [dim]{target}[/dim]")
            console.print(f"  [red]unreachable: {e}[/red]")
