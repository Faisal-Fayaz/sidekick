"""MCP client tests (fixes #107): stdio protocol, manager, agent wiring, CLI.
Offline — a fake MCP server script is spawned from tmp."""

import json
import sys

import pytest

import sk.mcp_client as mc

FAKE_SERVER_SRC = '''
import json, sys, time

def reply(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = {"code": -32601, "message": error}
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\\n")
    sys.stdout.flush()

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        method = req.get("method", "")
        rid = req.get("id")
        if method == "initialize":
            reply(rid, {"protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "fake", "version": "0"}})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            reply(rid, {"tools": [
                {"name": "echo", "description": "Echo a message",
                 "inputSchema": {"type": "object",
                                "properties": {"message": {"type": "string"}}}},
                {"name": "slow", "description": "Sleeps",
                 "inputSchema": {"type": "object"}},
                {"name": "boom", "description": "Fails",
                 "inputSchema": {"type": "object"}},
            ]})
        elif method == "tools/call":
            params = req.get("params", {}) or {}
            tool = params.get("name", "")
            args = params.get("arguments", {}) or {}
            if tool == "echo":
                reply(rid, {"content": [{"type": "text",
                                         "text": "echo:" + str(args.get("message", ""))}]})
            elif tool == "slow":
                time.sleep(30)
                reply(rid, {"content": [{"type": "text", "text": "slow done"}]})
            elif tool == "boom":
                reply(rid, {"content": [{"type": "text", "text": "kaput"}],
                            "isError": True})
            else:
                reply(rid, error="unknown tool " + tool)
        else:
            if rid is not None:
                reply(rid, error="unknown method " + method)

main()
'''


@pytest.fixture()
def fake_server(tmp_path):
    p = tmp_path / "mcp_fake_server.py"
    p.write_text(FAKE_SERVER_SRC)
    yield str(p)


@pytest.fixture(autouse=True)
def _no_leaked_servers():
    yield
    mc.close_all()


def _spec(name, server_path, timeout=10):
    return {
        "name": name,
        "command": sys.executable,
        "args": [server_path],
        "env": {},
        "timeout": timeout,
    }


def test_connect_list_and_call(fake_server):
    with mc.MCPClient("demo", sys.executable, [fake_server], {}, 10) as client:
        assert client.is_alive()
        tools = client.list_tools()
        assert sorted(t["name"] for t in tools) == ["boom", "echo", "slow"]
        assert client.call_tool("echo", {"message": "hi"}) == "echo:hi"
    assert not client.is_alive()


def test_call_iserror_and_unknown_tool(fake_server):
    with mc.MCPClient("demo", sys.executable, [fake_server], {}, 10) as client:
        out = client.call_tool("boom", {})
        assert out.startswith("Error:") and "kaput" in out
        assert client.call_tool("nope", {}).startswith("Error:")


def test_call_timeout(fake_server):
    client = mc.MCPClient("demo", sys.executable, [fake_server], {}, 1)
    try:
        client.connect()
        out = client.call_tool("slow", {})
        assert out.startswith("Error:") and "timed out" in out
    finally:
        client.close()


def test_bad_command_raises():
    client = mc.MCPClient("x", "/nonexistent/mcp-binary-xyz", [], {}, 5)
    with pytest.raises(RuntimeError):
        client.connect()
    client.close()
