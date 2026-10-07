"""MCP tools/list caching (#314).

approval_tools() is consulted once per gated call in a batch and
tools_schema() once per step, and both reached mcp_schema_extra(), which ran a
tools/list round trip against every configured server every time. These tests
count actual wire requests, because the claim is about round trips rather than
about a flag being set.
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import sk.mcp_client as mc
import sk.mcp_client as mcp_client_mod
import sk.mcp_server as mcp_server_mod

TOOLS = [
    {"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}},
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
        self.server.seen.append(method)
        if method == "initialize":
            self._json(200, self._result(rid, {"protocolVersion": "2024-11-05"}), session="s1")
            return
        if method == "notifications/initialized":
            self._json(202, None)
            return
        if method == "tools/list":
            if mode == "error":
                self._json(500, {"error": "boom"})
                return
            self._json(200, self._result(rid, {"tools": TOOLS}), session="s1")
            return
        if method == "tools/call":
            params = req.get("params", {}) or {}
            self._json(
                200,
                self._result(
                    rid, {"content": [{"type": "text", "text": "echo:" + str(params.get("name"))}]}
                ),
                session="s1",
            )
            return
        self._json(200, {"jsonrpc": "2.0", "id": rid, "error": {"message": "unknown"}})


@pytest.fixture()
def http_server():
    servers = []

    def _make(mode="json"):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        srv.mode = mode
        srv.seen = []
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


def _url(srv):
    return f"http://127.0.0.1:{srv.server_port}/mcp"


def _client(srv):
    return mc.MCPHttpClient("web", _url(srv), {}, 10)


def _lists(srv):
    return [m for m in srv.seen if m == "tools/list"]


# --- the fix ---------------------------------------------------------------


def test_repeat_list_tools_is_one_round_trip(http_server):
    srv = http_server()
    with _client(srv) as c:
        for _ in range(4):
            assert sorted(t["name"] for t in c.list_tools()) == ["boom", "echo"]
    assert len(_lists(srv)) == 1


def test_cache_survives_a_tool_call(http_server):
    """A call must not read as a signal that the schema changed."""
    srv = http_server()
    with _client(srv) as c:
        c.list_tools()
        c.call_tool("echo", {"message": "hi"})
        c.list_tools()
    assert len(_lists(srv)) == 1


def test_approval_tools_across_a_batch_is_one_round_trip(http_server, monkeypatch):
    """The actual #314 shape: four gated calls, two schema reads, one fetch."""
    srv = http_server()
    monkeypatch.setattr(
        mc,
        "load_servers",
        lambda: [
            {
                "name": "web",
                "command": "",
                "args": [],
                "env": {},
                "url": _url(srv),
                "headers": {},
                "timeout": 10,
            }
        ],
    )
    from sk.tools.registry import approval_tools, tools_schema

    try:
        for _ in range(4):
            approval_tools()
        tools_schema()
        assert "mcp__web__echo" in approval_tools()
    finally:
        mc.close_all()
    assert len(_lists(srv)) == 1


def test_concurrent_readers_fetch_once(http_server):
    """approval_tools() can be reached from a multi-threaded tool batch."""
    srv = http_server()
    c = _client(srv)
    c.connect()
    try:
        seen: list[list[dict]] = []
        barrier = threading.Barrier(4)

        def grab():
            barrier.wait()
            seen.append(c.list_tools())

        threads = [threading.Thread(target=grab) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
    finally:
        c.close()
    assert len(seen) == 4
    assert all(len(r) == 2 for r in seen)
    assert len(_lists(srv)) == 1


# --- what must still work --------------------------------------------------


def test_failure_is_never_cached(http_server):
    """mcp_schema_extra() skips a server by catching a raise; caching that
    raise would make one transient failure permanently remove the server."""
    srv = http_server("error")
    c = _client(srv)
    c.connect()
    try:
        for _ in range(3):
            with pytest.raises(RuntimeError):
                c.list_tools()
    finally:
        c.close()
    assert len(_lists(srv)) == 3


def test_recovery_after_a_failed_fetch(http_server):
    """A fetch that raises must leave nothing cached, so the next call retries.

    This is the property the cooldown in get_client() cannot provide: cooldown
    is keyed on the server failing to connect, but a tools/list that fails after
    a good handshake is invisible to it.
    """
    srv = http_server()
    c = _client(srv)
    c.connect()
    real = c._fetch_tools
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return real()

    c._fetch_tools = flaky
    try:
        with pytest.raises(RuntimeError, match="transient"):
            c.list_tools()
        assert sorted(t["name"] for t in c.list_tools()) == ["boom", "echo"]
        # A third read is served from cache, so the retry was not also the last.
        assert sorted(t["name"] for t in c.list_tools()) == ["boom", "echo"]
    finally:
        c.close()
    # The first attempt raised before touching the wire, so one real fetch.
    assert len(_lists(srv)) == 1
    assert len(calls) == 2


def test_invalidate_refetches(http_server):
    srv = http_server()
    with _client(srv) as c:
        c.list_tools()
        c.invalidate_tools()
        c.list_tools()
    assert len(_lists(srv)) == 2


def test_close_invalidates(http_server):
    srv = http_server()
    c = _client(srv)
    c.connect()
    c.list_tools()
    c.close()
    c.connect()
    c.list_tools()
    c.close()
    assert len(_lists(srv)) == 2


def test_reconnect_refetches(http_server):
    """A fresh connection may legitimately present a different tool list."""
    srv = http_server()
    c = _client(srv)
    c.connect()
    c.list_tools()
    c.close()
    c.connect()
    c.list_tools()
    c.close()
    assert len(_lists(srv)) == 2


def test_caller_gets_its_own_list(http_server):
    """The list is per-caller, so appending or sorting cannot corrupt the cache.

    The tool dicts inside are shared and read-only by contract; mcp_schema_extra()
    reads name/description and builds new dicts, so nothing in the tree mutates
    them, and deep-copying them per call would cost more than the round trip.
    """
    srv = http_server()
    with _client(srv) as c:
        first = c.list_tools()
        first.append({"name": "injected"})
        first.reverse()
        assert [t["name"] for t in c.list_tools()] == ["echo", "boom"]
    assert len(_lists(srv)) == 1


def test_tool_dispatch_still_works_with_a_cache(http_server):
    srv = http_server()
    with _client(srv) as c:
        c.list_tools()
        assert c.call_tool("echo", {"message": "hi"}) == "echo:echo"
        assert c.call_tool("echo", {"message": "again"}) == "echo:echo"
    assert len(_lists(srv)) == 1


# --- stdio transport -------------------------------------------------------

COUNTING_SERVER_SRC = """
import json, sys

COUNT_FILE = sys.argv[1]

def reply(rid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\\n")
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
            reply(rid, {"protocolVersion": "2024-11-05"})
        elif method == "tools/list":
            with open(COUNT_FILE, "a") as f:
                f.write("x")
            reply(rid, {"tools": [
                {"name": "echo", "description": "Echo",
                 "inputSchema": {"type": "object"}},
            ]})
        elif method == "tools/call":
            reply(rid, {"content": [{"type": "text", "text": "ok"}]})
        elif rid is not None:
            reply(rid, {})

main()
"""


@pytest.fixture()
def counting_server(tmp_path):
    src = tmp_path / "counting_server.py"
    src.write_text(COUNTING_SERVER_SRC)
    counter = tmp_path / "count.txt"
    counter.write_text("")
    spec = {
        "name": "c",
        "command": sys.executable,
        "args": [str(src), str(counter)],
        "env": {},
        "timeout": 10,
    }
    yield spec, counter


def _count(counter):
    return len(counter.read_text())


def test_stdio_tool_list_cached(counting_server):
    spec, counter = counting_server
    c = mc.MCPClient("c", spec["command"], spec["args"], {}, 10)
    c.connect()
    try:
        for _ in range(3):
            assert [t["name"] for t in c.list_tools()] == ["echo"]
    finally:
        c.close()
    assert _count(counter) == 1


def test_stdio_close_invalidates(counting_server):
    spec, counter = counting_server
    c = mc.MCPClient("c", spec["command"], spec["args"], {}, 10)
    c.connect()
    try:
        c.list_tools()
        c.close()
        c.connect()
        c.list_tools()
    finally:
        c.close()
    assert _count(counter) == 2


def test_two_clients_do_not_share_a_cache(counting_server):
    spec, counter = counting_server
    a = mc.MCPClient("c", spec["command"], spec["args"], {}, 10)
    b = mc.MCPClient("c", spec["command"], spec["args"], {}, 10)
    a.connect()
    b.connect()
    try:
        assert [t["name"] for t in a.list_tools()] == ["echo"]
        assert [t["name"] for t in b.list_tools()] == ["echo"]
        assert [t["name"] for t in a.list_tools()] == ["echo"]
    finally:
        a.close()
        b.close()
    assert _count(counter) == 2


# --- one definition of the shared surface (#376) ---------------------------


def test_transports_share_call_tool_and_exit():
    """`call_tool` was defined identically in both classes, so a fix had to be
    made twice and a miss was silent. The only real difference between the
    transports is how `_request` moves bytes."""
    assert "call_tool" not in mcp_client_mod.MCPClient.__dict__
    assert "call_tool" not in mcp_client_mod.MCPHttpClient.__dict__
    assert mcp_client_mod.MCPClient.call_tool is mcp_client_mod.MCPHttpClient.call_tool
    assert mcp_client_mod.MCPClient.__exit__ is mcp_client_mod.MCPHttpClient.__exit__


def test_protocol_version_has_one_definition():
    """It was a bare literal in both mcp_client.py and mcp_server.py, so a bump
    that missed one produced a client that silently failed to initialize against
    our own server."""
    assert mcp_client_mod.PROTOCOL_VERSION == mcp_server_mod.PROTOCOL_VERSION
    lit = 'PROTOCOL_VERSION = "'
    assert lit not in mcp_client_src(), f"client still hardcodes the literal:\n{mcp_client_src()}"


def mcp_client_src() -> str:
    import pathlib

    import sk.mcp_client as m

    return pathlib.Path(m.__file__).read_text()
