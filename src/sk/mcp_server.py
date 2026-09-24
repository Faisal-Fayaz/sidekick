"""MCP server over stdio: exposes the 17 sidekick tools to MCP clients.

Hand-rolled JSON-RPC 2.0, stdlib only (json + sys). No network, no new deps.
Execution flows through dispatch_tool, so SSRF guards, write blocklists and
hard-refusals apply unchanged. Stdout carries protocol only (logs go stderr).

Policy: read-only tools run; approval-gated tools (shell, writes, delete)
need --allow-writes, else a clean denied error (no user to prompt headless).
"""

from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "sidekick"


def _server_version() -> str:
    try:
        from . import __version__

        return __version__
    except Exception:
        return "unknown"


def mcp_tools() -> list[dict]:
    """All 17 tools converted to MCP shape (name/description/inputSchema)."""
    from .tools import TOOLS_SCHEMA

    out = []
    for entry in TOOLS_SCHEMA:
        fn = entry.get("function", entry) if isinstance(entry, dict) else {}
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        params = fn.get("parameters") or {"type": "object", "properties": {}}
        out.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "inputSchema": params,
            }
        )
    return out


def _call_tool(name: str, args: dict, allow_writes: bool) -> dict:
    """Execute one tool call. Returns an MCP content result (isError on refusal)."""
    from .tools import APPROVAL_TOOLS, dispatch_tool

    if not isinstance(args, dict):
        return {
            "content": [{"type": "text", "text": "Error: arguments must be an object."}],
            "isError": True,
        }
    known = {t["name"] for t in mcp_tools()}
    if name not in known:
        return {
            "content": [{"type": "text", "text": f"Error: unknown tool '{name}'."}],
            "isError": True,
        }
    if name in APPROVAL_TOOLS and not allow_writes:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Denied: '{name}' needs approval — restart with --allow-writes.",
                }
            ],
            "isError": True,
        }
    try:
        result = dispatch_tool(name, args)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
    failed = result.startswith("Error") or "blocked" in result[:60].lower()
    return {"content": [{"type": "text", "text": result}], "isError": failed}


def _error(req_id, code: int, message: str) -> dict:
    resp: dict = {"jsonrpc": "2.0", "error": {"code": code, "message": message}}
    if req_id is not None:
        resp["id"] = req_id
    return resp


def handle_message(msg: dict, allow_writes: bool = False) -> dict | None:
    """Route one JSON-RPC message. Returns response dict, or None for notifications."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request.")
    method = msg["method"]
    req_id = msg.get("id")
    params = msg.get("params", {})
    if not isinstance(params, dict):
        params = {}
    if method == "initialize":
        if req_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": _server_version()},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        if req_id is None:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": mcp_tools()}}
    if method == "tools/call":
        if req_id is None:
            return None
        name = params.get("name", "")
        args = params.get("arguments", {})
        return {"jsonrpc": "2.0", "id": req_id, "result": _call_tool(name, args, allow_writes)}
    if req_id is None:
        return None  # unknown notification: ignore silently
    return _error(req_id, -32601, f"Method not found: '{method}'.")


def handle_line(line: str, allow_writes: bool = False) -> str | None:
    """Parse one stdio line, route (single or batch), serialize the reply."""
    try:
        msg = json.loads(line)
    except Exception:
        return json.dumps(_error(None, -32700, "Parse error."))
    if isinstance(msg, list):
        resps = [handle_message(m, allow_writes) for m in msg]
        resps = [r for r in resps if r is not None]
        return json.dumps(resps) if resps else None
    resp = handle_message(msg, allow_writes)
    return json.dumps(resp) if resp is not None else None


def serve_stdio(allow_writes: bool = False) -> int:
    """Read JSON-RPC lines from stdin, write replies to stdout. Returns exit code."""
    print(
        f"sidekick mcp server (protocol {PROTOCOL_VERSION}, allow_writes={allow_writes})",
        file=sys.stderr,
        flush=True,
    )
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                out = handle_line(line, allow_writes)
            except Exception as e:  # never let one bad line kill the server
                out = json.dumps(_error(None, -32600, f"Internal error: {e}"))
            if out is not None:
                print(out, flush=True)
    except (BrokenPipeError, EOFError, KeyboardInterrupt):
        pass
    return 0
