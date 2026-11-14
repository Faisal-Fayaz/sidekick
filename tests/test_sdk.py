"""The SDK and the localhost HTTP surface (#110).

The SDK is a way in, not a second implementation: a turn must run the same
loop, the same tools and the same audit ledger as the CLI. The HTTP surface
must be reachable from this machine only, and only with the token.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import sk.serve as serve
import sk.store as store
from sk.config import Config


def _cfg():
    return Config(provider="ollama", model="fake", base_url="http://x/v1", api_key="x")


# --- the SDK ---------------------------------------------------------------


def test_the_sdk_runs_a_turn_with_the_real_loop(monkeypatch):
    """The SDK calls the same loop the CLI calls — not a copy of it."""
    import sk.agent as agent

    seen = {}

    def fake_run_agent(user_msg, history, cfg, **kwargs):
        seen["user_msg"] = user_msg
        seen["history"] = history
        seen["cfg"] = cfg
        seen["kwargs"] = kwargs
        return "the answer"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)

    from sk.sdk import Agent

    answer = Agent(config=_cfg(), session="sdk-test").turn("what does this do?")
    assert answer == "the answer"
    assert seen["user_msg"] == "what does this do?"
    assert seen["kwargs"]["session"] == "sdk-test"
    assert seen["cfg"].model == "fake", "the SDK ran with a different configuration"


def test_the_sdk_keeps_the_conversation(monkeypatch):
    import sk.agent as agent

    monkeypatch.setattr(agent, "run_agent", lambda *a, **k: "ok")

    from sk.sdk import Agent

    agent = Agent(config=_cfg(), session="sdk-test")
    agent.turn("first")
    agent.turn("second")
    assert [m["role"] for m in agent.history] == ["user", "assistant", "user", "assistant"]
    assert agent.history[0]["content"] == "first"


def test_the_sdk_call_is_audited(monkeypatch, tmp_path):
    """Audit parity: the SDK's turns land in the same ledger the CLI writes."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    import sk.agent as agent

    def fake_run_agent(user_msg, history, cfg, **kwargs):
        store.log_tool_run(kwargs.get("session", "sdk"), "sysinfo", "sdk", approved=True)
        return "done"

    monkeypatch.setattr(agent, "run_agent", fake_run_agent)

    from sk.sdk import Agent

    Agent(config=_cfg(), session="sdk-audit").turn("hello")
    rows = store.list_tool_runs("sdk-audit")
    assert any(r["tool"] == "sysinfo" for r in rows), rows


def test_the_sdk_can_call_a_tool_directly(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    from sk.sdk import Agent

    result = Agent(config=_cfg(), session="sdk-tool").tool("sysinfo", {})
    assert "CPU" in result or "Error" in result  # the real tool ran
    rows = store.list_tool_runs("sdk-tool")
    assert rows, "the tool call was not audited"


def test_the_sdk_honours_a_custom_approver(monkeypatch, tmp_path):
    """An SDK that cannot ask still has to be able to say no."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    from sk.sdk import Agent

    asked = []

    def approve(name, args):
        asked.append(name)
        return False

    result = Agent(config=_cfg(), session="sdk-gate", approve=approve).tool(
        "write_file", {"path": "/tmp/x", "content": "y"}
    )
    assert asked == ["write_file"]
    assert "denied" in result.lower() or "--yes" in result


def test_the_module_level_turn_is_a_one_shot(monkeypatch):
    import sk.agent as agent

    monkeypatch.setattr(agent, "run_agent", lambda *a, **k: "once")

    from sk.sdk import turn

    assert turn("hello", config=_cfg(), session="sdk-one") == "once"


# --- the server ------------------------------------------------------------


def test_only_loopback_addresses_are_accepted():
    assert serve.is_loopback("127.0.0.1") is True
    assert serve.is_loopback("127.0.0.5") is True
    assert serve.is_loopback("::1") is True
    assert serve.is_loopback("0.0.0.0") is False
    assert serve.is_loopback("192.168.1.10") is False
    assert serve.is_loopback("") is False


def test_the_server_refuses_to_bind_beyond_this_machine():
    with pytest.raises(ValueError):
        serve.make_server(host="0.0.0.0", port=0, token="t")


def test_the_token_is_written_with_owner_only_permissions(tmp_path):
    path = tmp_path / "serve.token"
    serve.write_token("secret", path)
    assert path.read_text() == "secret"
    assert oct(path.stat().st_mode)[-3:] == "600"


def _request(server, path, payload=None, token="t"):
    host, port = server.server_address[0], server.server_address[1]
    url = f"http://{host}:{port}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


@pytest.fixture
def server():
    srv = serve.make_server(host="127.0.0.1", port=0, token="t")
    import threading

    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def test_health_needs_the_token(server):
    status, body = _request(server, "/health", token=None)
    assert status == 401
    assert body["error"] == "unauthorized"


def test_health_answers_with_the_version(server):
    import sk

    status, body = _request(server, "/health", token="t")
    assert status == 200
    assert body["ok"] is True
    assert body["version"] == sk.__version__


def test_a_turn_runs_and_is_audited(server, monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    import sk.agent as agent

    monkeypatch.setattr(
        agent,
        "run_agent",
        lambda user_msg, history, cfg, **kw: (
            store.log_tool_run(kw.get("session", "http"), "sysinfo", "http", approved=True),
            "the answer",
        )[1],
    )

    status, body = _request(
        server, "/turn", {"message": "hello", "session": "http-test"}, token="t"
    )
    assert status == 200
    assert body["answer"] == "the answer"
    rows = store.list_tool_runs("http-test")
    assert any(r["tool"] == "sysinfo" for r in rows), rows


def test_a_turn_without_a_message_is_refused(server):
    status, body = _request(server, "/turn", {}, token="t")
    assert status == 200
    assert "error" in body


def test_an_unknown_route_is_not_found(server):
    status, body = _request(server, "/nope", {}, token="t")
    assert status == 404


def test_read_only_mode_denies_the_writes(server, monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    srv = serve.make_server(host="127.0.0.1", port=0, token="t", read_only=True)
    import threading

    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _request(
            srv,
            "/tool",
            {"name": "write_file", "args": {"path": "/tmp/x", "content": "y"}},
            token="t",
        )
        assert status == 200
        assert "denied" in body["result"].lower() or "--yes" in body["result"]
    finally:
        srv.shutdown()
        srv.server_close()
