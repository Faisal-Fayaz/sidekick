"""MCP client tests (fixes #107): stdio protocol, manager, agent wiring, CLI.
Offline — a fake MCP server script is spawned from tmp."""

import json
import sys

import pytest

import sk.mcp_client as mc

FAKE_SERVER_SRC = """
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
"""


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


def _write_config(tmp_path, monkeypatch, server_path=None, extra=""):
    import sk.config as config_mod

    d = tmp_path / "cfg"
    d.mkdir(exist_ok=True)
    body = ""
    if server_path is not None:
        body += (
            "[mcp_servers.demo]\n"
            f"command = '{sys.executable}'\n"
            f"args = ['{server_path}']\n"
            "timeout = 10\n"
            "trust = 'full'  # in-process test server; untrusted default would gate it (#303)\n"
        )
    (d / "config.toml").write_text(body + extra)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", d)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", d / "config.toml")
    monkeypatch.chdir(tmp_path)


def test_config_parsing(tmp_path, monkeypatch, fake_server):
    _write_config(
        tmp_path,
        monkeypatch,
        fake_server,
        "[mcp_servers.noname]\nargs = ['x']\n[mcp_servers.'weird name']\ncommand = 'x'\n",
    )
    from sk.config import Config

    servers = Config.load().mcp_servers
    assert len(servers) == 1
    demo = servers[0]
    assert demo["name"] == "demo" and demo["args"] == [fake_server]
    assert demo["timeout"] == 10.0


def test_schema_and_approval_include_mcp(tmp_path, monkeypatch, fake_server):
    _write_config(tmp_path, monkeypatch, fake_server)
    from sk.tools import approval_tools, tools_schema

    names = {e["function"]["name"] for e in tools_schema()}
    assert "mcp__demo__echo" in names
    assert "mcp__demo__echo" in approval_tools()


def test_dispatch_routes_to_server(tmp_path, monkeypatch, fake_server):
    _write_config(tmp_path, monkeypatch, fake_server)
    from sk.tools import dispatch_tool

    assert dispatch_tool("mcp__demo__echo", {"message": "yo"}) == "echo:yo"


def test_dispatch_unknown_server(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch)
    from sk.tools import dispatch_tool

    assert dispatch_tool("mcp__ghost__echo", {}).startswith("Error:")


def test_server_not_reserved_over_mcp(tmp_path, monkeypatch, fake_server):
    _write_config(tmp_path, monkeypatch, fake_server)
    from sk.mcp_server import mcp_tools

    assert not any(t["name"].startswith("mcp__") for t in mcp_tools())


def test_dead_server_degrades(tmp_path, monkeypatch):
    import sk.config as config_mod

    d = tmp_path / "cfg"
    d.mkdir(exist_ok=True)
    (d / "config.toml").write_text("[mcp_servers.dead]\ncommand = '/nonexistent/mcp-binary-xyz'\n")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", d)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", d / "config.toml")
    monkeypatch.chdir(tmp_path)
    from sk.tools import approval_tools, dispatch_tool, tools_schema

    assert not any("mcp__" in e["function"]["name"] for e in tools_schema())
    assert not any(n.startswith("mcp__") for n in approval_tools())
    # second pass exercises the failure cooldown branch
    assert not any("mcp__" in e["function"]["name"] for e in tools_schema())
    assert dispatch_tool("mcp__dead__echo", {}).startswith("Error:")


def test_cli_mcp_servers(tmp_path, monkeypatch, fake_server):
    _write_config(tmp_path, monkeypatch, fake_server)
    from typer.testing import CliRunner

    from sk.cli import app

    res = CliRunner().invoke(app, ["mcp-servers"])
    assert res.exit_code == 0, res.output
    flat = " ".join(res.output.split())  # Rich wraps console lines by width
    assert "demo" in flat and "3 tools" in flat and "echo" in flat


def test_cli_mcp_servers_empty(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch)
    from typer.testing import CliRunner

    from sk.cli import app

    res = CliRunner().invoke(app, ["mcp-servers"])
    assert res.exit_code == 0, res.output
    flat = " ".join(res.output.split())  # Rich wraps console lines by width
    assert "no MCP servers" in flat


def test_cli_mcp_servers_dead(tmp_path, monkeypatch):
    import sk.config as config_mod

    d = tmp_path / "cfg"
    d.mkdir(exist_ok=True)
    (d / "config.toml").write_text("[mcp_servers.dead]\ncommand = '/nonexistent/x'\n")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", d)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", d / "config.toml")
    monkeypatch.chdir(tmp_path)
    from typer.testing import CliRunner

    from sk.cli import app

    res = CliRunner().invoke(app, ["mcp-servers"])
    assert res.exit_code == 0, res.output
    assert "dead" in res.output and "unreachable" in res.output
