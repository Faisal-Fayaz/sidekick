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


def test_tools_list_all_nineteen():
    out = _req("tools/list")
    tools = out["result"]["tools"]
    assert len(tools) == 19
    for t in tools:
        assert set(t) >= {"name", "description", "inputSchema"}
        assert isinstance(t["inputSchema"], dict)
    names = {t["name"] for t in tools}
    assert {
        "sysinfo",
        "shell",
        "shell_session",
        "write_file",
        "read_url",
        "skill",
        "generate_image",
    } <= names


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
    assert out[0]["id"] == 1 and len(out[0]["result"]["tools"]) == 19
    assert out[1]["result"]["isError"] is False


def _iso_hooks(tmp_path, monkeypatch):
    import sk.config as config_mod
    import sk.hooks as hooks_mod

    cfgdir = tmp_path / ".sidekick"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfgdir)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfgdir / "config.toml")
    monkeypatch.chdir(tmp_path)
    hooks_mod._started_sessions.clear()
    return cfgdir


def _toml_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _hook_cmd(tmp_path, body):
    import shlex
    import sys

    p = tmp_path / f"hook_{len(list(tmp_path.glob('hook_*.py')))}.py"
    p.write_text(body)
    return shlex.join([sys.executable, str(p)])


ALLOW_SRC = "import json,sys; json.load(sys.stdin); print(json.dumps({'decision': 'allow'}))"


def test_call_denied_by_prehook(tmp_path, monkeypatch):
    """Denying PreToolUse hook refuses the served call with isError + hook reason."""
    import json as _json

    cfgdir = _iso_hooks(tmp_path, monkeypatch)
    dump = tmp_path / "payload.json"
    deny_src = (
        "import json,sys; p=json.load(sys.stdin); "
        f"open({str(dump)!r},'w').write(json.dumps(p)); "
        "print(json.dumps({'decision': 'deny', 'reason': 'served traffic blocked'}))"
    )
    cfgdir.mkdir(parents=True, exist_ok=True)
    (cfgdir / "config.toml").write_text(
        f"[[hooks.PreToolUse]]\ncommand = {_toml_str(_hook_cmd(tmp_path, deny_src))}\n"
    )
    out = _req("tools/call", {"name": "list_dir", "arguments": {"path": "/tmp"}})
    assert out["result"]["isError"] is True
    text = out["result"]["content"][0]["text"]
    assert text.startswith("Denied by hook") and "served traffic blocked" in text
    payload = _json.loads(dump.read_text())
    assert payload["session"] == "mcp" and payload["tool"] == "list_dir"
    assert payload["args"] == {"path": "/tmp"}


def test_call_allowed_by_prehook(tmp_path, monkeypatch):
    """Allowing hook leaves served calls untouched."""
    cfgdir = _iso_hooks(tmp_path, monkeypatch)
    cfgdir.mkdir(parents=True, exist_ok=True)
    (cfgdir / "config.toml").write_text(
        f"[[hooks.PreToolUse]]\ncommand = {_toml_str(_hook_cmd(tmp_path, ALLOW_SRC))}\n"
    )
    out = _req("tools/call", {"name": "list_dir", "arguments": {"path": "/tmp"}})
    assert out["result"]["isError"] is False
    assert "/tmp" in out["result"]["content"][0]["text"]


def test_call_no_hooks_behaves_as_before(tmp_path, monkeypatch):
    """Empty hooks table: approval semantics unchanged (read ok, write refused)."""
    cfgdir = _iso_hooks(tmp_path, monkeypatch)
    cfgdir.mkdir(parents=True, exist_ok=True)
    (cfgdir / "config.toml").write_text("")
    out = _req("tools/call", {"name": "list_dir", "arguments": {"path": "/tmp"}})
    assert out["result"]["isError"] is False
    out = _req(
        "tools/call", {"name": "write_file", "arguments": {"path": "/tmp/x", "content": "hi"}}
    )
    assert out["result"]["isError"] is True
    assert "allow-writes" in out["result"]["content"][0]["text"]
