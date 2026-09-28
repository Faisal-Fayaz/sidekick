"""Remote MCP transport tests (fixes #154): Streamable HTTP incl. SSE,
session handshake, timeouts, errors, config, dispatch, CLI. Offline —
http.server fixtures on 127.0.0.1."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import sk.mcp_client as mc

TOOLS = [
    {"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}},
    {"name": "slow", "description": "Sleeps", "inputSchema": {"type": "object"}},
    {"name": "boom", "description": "Fails", "inputSchema": {"type": "object"}},
]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj, session=None):
        body = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if session:
            self.send_header("Mcp-Session-Id", session)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _sse(self, obj):
        body = ("data: " + json.dumps(obj) + "\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _result(self, rid, result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            req = {}
        method = req.get("method", "")
        rid = req.get("id")
        mode = getattr(self.server, "mode", "json")
        self.server.seen.append({"method": method, "headers": dict(self.headers), "mode": mode})
        if method == "initialize":
            self._json(
                rid and 200 or 200,
                self._result(rid, {"protocolVersion": "2024-11-05"}),
                session="sess-1",
            )
            return
        if method == "notifications/initialized":
            self._json(202, None)
            return
        if mode == "flaky" and not getattr(self.server, "flaked", False):
            self.server.flaked = True
            self._json(404, {"jsonrpc": "2.0", "id": rid, "error": {"message": "no session"}})
            return
        if method == "tools/list":
            if mode == "error":
                self._json(500, {"error": "boom"})
                return
            if mode == "sse":
                self._sse(self._result(rid, {"tools": TOOLS}))
            else:
                self._json(200, self._result(rid, {"tools": TOOLS}), session="sess-1")
            return
        if method == "tools/call":
            params = req.get("params", {}) or {}
            tool = params.get("name", "")
            args = params.get("arguments", {}) or {}
            if mode == "sleep":
                import time

                time.sleep(30)
            if mode == "error":
                self._json(500, {"error": "boom"})
                return
            if tool == "echo":
                content = {
                    "content": [{"type": "text", "text": "echo:" + str(args.get("message", ""))}]
                }
            elif tool == "boom":
                content = {"content": [{"type": "text", "text": "kaput"}], "isError": True}
            else:
                self._json(200, {"jsonrpc": "2.0", "id": rid, "error": {"message": "unknown"}})
                return
            if mode == "sse":
                self._sse(self._result(rid, content))
            else:
                self._json(200, self._result(rid, content), session="sess-1")
            return
        self._json(200, {"jsonrpc": "2.0", "id": rid, "error": {"message": "unknown method"}})


@pytest.fixture()
def http_server():
    servers = []

    def _make(mode="json"):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        srv.mode = mode
        srv.seen = []
        srv.flaked = False
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv

    yield _make
    for srv in servers:
        try:
            srv.shutdown()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _no_leaked_clients():
    yield
    mc.close_all()


def _url(srv):
    return f"http://127.0.0.1:{srv.server_port}/mcp"


def _spec(name, url, timeout=10, headers=None):
    return {
        "name": name,
        "command": "",
        "args": [],
        "env": {},
        "url": url,
        "headers": headers or {},
        "timeout": timeout,
    }


def test_http_list_and_call(http_server):
    url = _url(http_server())
    with mc.MCPHttpClient("web", url, {}, 10) as client:
        assert client.is_alive()
        assert sorted(t["name"] for t in client.list_tools()) == ["boom", "echo", "slow"]
        assert client.call_tool("echo", {"message": "hi"}) == "echo:hi"
        assert client.call_tool("boom", {}).startswith("Error:")
    assert not client.is_alive()


def test_http_sse_variant(http_server):
    url = _url(http_server("sse"))
    with mc.MCPHttpClient("web", url, {}, 10) as client:
        assert len(client.list_tools()) == 3
        assert client.call_tool("echo", {"message": "yo"}) == "echo:yo"


def test_http_session_header_sent(http_server):
    srv = http_server()
    with mc.MCPHttpClient("web", _url(srv), {}, 10) as client:
        client.list_tools()
    posts = [s for s in srv.seen if s["method"] == "tools/list"]
    assert posts and posts[0]["headers"].get("Mcp-Session-Id") == "sess-1"


def test_http_headers_passthrough(http_server):
    srv = http_server()
    with mc.MCPHttpClient("web", _url(srv), {"Authorization": "Bearer x"}, 10) as client:
        client.list_tools()
    assert srv.seen[0]["headers"].get("Authorization") == "Bearer x"


def test_http_timeout(http_server):
    url = _url(http_server("sleep"))
    client = mc.MCPHttpClient("web", url, {}, 1)
    try:
        client.connect()
        out = client.call_tool("echo", {"message": "hi"})
        assert out.startswith("Error:") and "timed out" in out
    finally:
        client.close()


def test_http_500_degrades(http_server):
    url = _url(http_server("error"))
    with mc.MCPHttpClient("web", url, {}, 10) as client:
        with pytest.raises(RuntimeError):
            client.list_tools()
        assert client.call_tool("echo", {}).startswith("Error:")


def test_http_flaky_session_reinits(http_server):
    srv = http_server("flaky")
    with mc.MCPHttpClient("web", _url(srv), {}, 10) as client:
        assert len(client.list_tools()) == 3
    inits = [s for s in srv.seen if s["method"] == "initialize"]
    assert len(inits) == 2


def _write_config(tmp_path, monkeypatch, body):
    import sk.config as config_mod

    d = tmp_path / "cfg"
    d.mkdir(exist_ok=True)
    (d / "config.toml").write_text(body)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", d)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", d / "config.toml")
    monkeypatch.chdir(tmp_path)
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def _toml_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def test_config_url_headers_and_refusals(tmp_path, monkeypatch, http_server):
    url = _url(http_server())
    _write_config(
        tmp_path,
        monkeypatch,
        "[mcp_servers.web]\n"
        f"url = {_toml_str(url)}\n"
        '[mcp_servers.web.headers]\nAuthorization = "Bearer x"\n'
        '[mcp_servers.both]\ncommand = "x"\n'
        f"url = {_toml_str(url)}\n"
        '[mcp_servers.bad]\nurl = "ftp://x"\n',
    )
    from sk.config import Config

    servers = {s["name"]: s for s in Config.load().mcp_servers}
    assert set(servers) == {"web"}
    assert servers["web"]["headers"] == {"Authorization": "Bearer x"}
    assert servers["web"]["command"] == ""


def test_dispatch_end_to_end_url(tmp_path, monkeypatch, http_server):
    url = _url(http_server())
    _write_config(tmp_path, monkeypatch, f"[mcp_servers.web]\nurl = {_toml_str(url)}\n")
    from sk.tools import approval_tools, dispatch_tool, tools_schema

    names = {e["function"]["name"] for e in tools_schema()}
    assert "mcp__web__echo" in names
    assert "mcp__web__echo" in approval_tools()
    assert dispatch_tool("mcp__web__echo", {"message": "yo"}) == "echo:yo"


def test_cli_mcp_servers_url(tmp_path, monkeypatch, http_server):
    url = _url(http_server())
    _write_config(tmp_path, monkeypatch, f"[mcp_servers.web]\nurl = {_toml_str(url)}\n")
    from typer.testing import CliRunner

    from sk.cli import app

    res = CliRunner().invoke(app, ["mcp-servers"])
    assert res.exit_code == 0, res.output
    flat = " ".join(res.output.split())
    assert "web" in flat and "3 tools" in flat
