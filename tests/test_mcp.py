"""MCP server tests: protocol units over handle_line (no subprocess, no network)."""

import json

import sk.mcp_server as mcp


def _req(method, params=None, req_id=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if req_id is not None:
        msg["id"] = req_id
    return json.loads(mcp.handle_line(json.dumps(msg)) or "null")


def test_initialize_handshake():
    out = _req("initialize", {"protocolVersion": "2024-11-05"})
    assert out["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert out["result"]["serverInfo"]["name"] == "sidekick"
    assert "tools" in out["result"]["capabilities"]


def test_initialized_notification_silent():
    assert (
        mcp.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        is None
    )


def test_tools_list_all_seventeen():
    out = _req("tools/list")
    tools = out["result"]["tools"]
    assert len(tools) == 17
    for t in tools:
        assert set(t) >= {"name", "description", "inputSchema"}
        assert isinstance(t["inputSchema"], dict)
    names = {t["name"] for t in tools}
    assert {"sysinfo", "shell", "write_file", "read_url", "skill"} <= names


def test_call_read_tool():
    out = _req("tools/call", {"name": "list_dir", "arguments": {"path": "/tmp"}})
    assert out["result"]["isError"] is False
    assert "/tmp" in out["result"]["content"][0]["text"]


def test_call_write_denied_without_flag():
    out = _req(
        "tools/call", {"name": "write_file", "arguments": {"path": "/tmp/x", "content": "hi"}}
    )
    assert out["result"]["isError"] is True
    assert "allow-writes" in out["result"]["content"][0]["text"]


def test_call_write_allowed_with_flag():
    import tempfile

    from sk.mcp_server import handle_message

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        path = tf.name
    out = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": path, "content": "hi"}},
        },
        allow_writes=True,
    )
    assert out["result"]["isError"] is False
    assert "Wrote" in out["result"]["content"][0]["text"]


def test_call_unknown_tool():
    out = _req("tools/call", {"name": "nope", "arguments": {}})
    assert out["result"]["isError"] is True
    assert "unknown tool" in out["result"]["content"][0]["text"]


def test_call_non_object_args():
    out = _req("tools/call", {"name": "list_dir", "arguments": [1, 2]})
    assert out["result"]["isError"] is True


def test_unknown_method_and_malformed():
    out = _req("whatever", {}, req_id=7)
    assert out["error"]["code"] == -32601
    raw = mcp.handle_line("{not json")
    assert json.loads(raw)["error"]["code"] == -32700
    raw = mcp.handle_line(json.dumps({"nope": 1}))
    assert json.loads(raw)["error"]["code"] == -32600


def test_batch_mixed():
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "exec", "arguments": {"cmd": "pwd"}},
        },
    ]
    out = json.loads(mcp.handle_line(json.dumps(batch)))
    assert isinstance(out, list) and len(out) == 2  # notification answered with silence
    assert out[0]["id"] == 1 and len(out[0]["result"]["tools"]) == 17
    assert out[1]["result"]["isError"] is False
