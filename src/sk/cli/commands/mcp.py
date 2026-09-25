"""CLI commands: MCP server over stdio."""

from __future__ import annotations

import typer

from ..base import _cfg, app


@app.command()
def mcp(
    allow_writes: bool = typer.Option(
        False, "--allow-writes", help="Allow shell/writes/delete (else denied)"
    ),
):
    """Serve the 17 tools over MCP stdio (for Claude Desktop, IDEs)."""
    from sk.mcp_server import serve_stdio

    _cfg()  # establish config + memory namespace; stdout stays protocol-clean
    if allow_writes:
        import sys

        print(
            "mcp: writes allowed — only expose to clients you trust.", file=sys.stderr, flush=True
        )
    raise typer.Exit(serve_stdio(allow_writes))
