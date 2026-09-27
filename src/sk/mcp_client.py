"""MCP client: consume external MCP servers as agent tools. Stdlib only.

v1 scope: local stdio servers only (no remote/streamable transports).
JSON-RPC 2.0 over newline-delimited stdio, protocol 2024-11-05.
Spawning is per-process cached; failures cool down briefly so a dead server
can never stall prompt building. Entry points used by the agent never raise.
"""

from __future__ import annotations

import atexit
import itertools
import json
import os
import queue
import subprocess
import threading
import time

PROTOCOL_VERSION = "2024-11-05"
PREFIX = "mcp__"
FAILED_COOLDOWN_S = 60.0


def _version() -> str:
    try:
        from sk import __version__

        return str(__version__)
    except Exception:
        return "0.0"


class MCPClient:
    """One stdio MCP server process. Call via `with` or connect()/close()."""

    def __init__(
        self,
        name: str,
        command: str,
        args: tuple | list = (),
        env: dict | None = None,
        timeout: float = 30.0,
    ):
        self.name = name
        self.command = command
        self.args = [str(a) for a in (args or [])]
        self.env = dict(env or {})
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, queue.Queue] = {}
        self._wlock = threading.Lock()

    def __enter__(self) -> "MCPClient":
        self.connect()
        return self

    def __exit__(self, *a: object) -> None:
        self.close()

    def is_alive(self) -> bool:
        p = self._proc
        return p is not None and p.poll() is None

    def connect(self) -> None:
        """Spawn + initialize handshake. Raises RuntimeError."""
        if self.is_alive():
            return
        self.close()
        full_env = dict(os.environ)
        full_env.update(self.env)
        try:
            proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=full_env,
                text=True,
                bufsize=1,
            )
        except Exception as e:
            raise RuntimeError(f"cannot spawn: {e}")
        self._proc = proc
        reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader = reader
        reader.start()
        try:
            self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "sidekick", "version": _version()},
                },
                self.timeout,
            )
            self._notify("notifications/initialized")
        except Exception:
            self.close()
            raise

    def list_tools(self) -> list[dict]:
        """Normalized [{name, description, inputSchema}]. Raises RuntimeError."""
        result = self._request("tools/list", {}, self.timeout)
        tools = result.get("tools", [])
        out = []
        if isinstance(tools, list):
            for t in tools:
                if not isinstance(t, dict) or not str(t.get("name", "")).strip():
                    continue
                schema = t.get("inputSchema")
                out.append(
                    {
                        "name": str(t["name"]),
                        "description": str(t.get("description", "") or ""),
                        "inputSchema": schema if isinstance(schema, dict) else {"type": "object"},
                    }
                )
        return out

    def call_tool(self, tool: str, arguments: dict | None = None) -> str:
        """Call a tool. Returns text (or an 'Error: ...' string). Never raises."""
        try:
            result = self._request(
                "tools/call",
                {"name": tool, "arguments": dict(arguments or {})},
                self.timeout,
            )
        except Exception as e:
            return f"Error: mcp {self.name}.{tool}: {e}"
        texts = []
        content = result.get("content", [])
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    texts.append(str(b.get("text", "")))
        out = "\n".join(texts).strip() or "(empty result)"
        if result.get("isError"):
            return "Error: " + out[:4000]
        return out[:8000]

    def close(self) -> None:
        """Terminate the server. Never raises."""
        try:
            proc, self._proc = self._proc, None
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=2)
                        except Exception:
                            proc.kill()
                except Exception:
                    pass
                for stream in (proc.stdin, proc.stdout):
                    try:
                        if stream is not None:
                            stream.close()
                    except Exception:
                        pass
            reader, self._reader = self._reader, None
            if reader is not None and reader.is_alive():
                reader.join(timeout=2)
        except Exception:
            pass

    def _notify(self, method: str) -> None:
        try:
            with self._wlock:
                if self._proc is not None and self._proc.stdin is not None:
                    self._proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
                    self._proc.stdin.flush()
        except Exception:
            pass

    def _request(self, method: str, params: dict, timeout: float) -> dict:
        """Send + await the matching response id. Raises RuntimeError."""
        rid = next(self._ids)
        box: queue.Queue = queue.Queue()
        self._pending[rid] = box
        try:
            payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
            with self._wlock:
                proc = self._proc
                if proc is None or proc.stdin is None:
                    raise RuntimeError("not connected")
                try:
                    proc.stdin.write(json.dumps(payload) + "\n")
                    proc.stdin.flush()
                except Exception as e:
                    raise RuntimeError(f"write failed: {e}")
            try:
                msg = box.get(timeout=max(0.5, timeout))
            except queue.Empty:
                raise RuntimeError(f"timed out after {timeout:g}s")
        finally:
            self._pending.pop(rid, None)
        if not isinstance(msg, dict):
            raise RuntimeError("bad response")
        err = msg.get("error")
        if err is not None:
            detail = err.get("message", err) if isinstance(err, dict) else err
            raise RuntimeError(str(detail)[:300])
        result = msg.get("result", {})
        return result if isinstance(result, dict) else {}

    def _read_loop(self) -> None:
        try:
            proc = self._proc
            stream = proc.stdout if proc is not None else None
            if stream is None:
                return
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                mid = msg.get("id")
                if mid is None:
                    continue  # server notification; nothing to route
                box = self._pending.get(mid)
                if box is not None:
                    try:
                        box.put(msg)
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            dead = {"jsonrpc": "2.0", "error": {"message": "server closed stdout"}}
            for box in list(self._pending.values()):
                try:
                    box.put(dict(dead))
                except Exception:
                    pass


_clients: dict[str, MCPClient] = {}
_clients_lock = threading.Lock()
_failed_until: dict[str, float] = {}


def _spec_key(spec: dict) -> str:
    try:
        return spec["name"] + "\x00" + json.dumps(
            {k: spec.get(k) for k in ("command", "args", "env", "timeout")}, sort_keys=True
        )
    except Exception:
        return spec.get("name", "?") + "\x00?"


def get_client(spec: dict) -> MCPClient:
    """Cached connected client for a server spec. Raises RuntimeError."""
    key = _spec_key(spec)
    with _clients_lock:
        now = time.monotonic()
        if _failed_until.get(key, 0) > now:
            raise RuntimeError("server recently failed (cooling down)")
        client = _clients.get(key)
        if client is not None and not client.is_alive():
            try:
                client.close()
            except Exception:
                pass
            client = None
        if client is None:
            client = MCPClient(
                spec["name"],
                spec["command"],
                spec.get("args", ()),
                spec.get("env", {}),
                float(spec.get("timeout", 30) or 30),
            )
            try:
                client.connect()
            except Exception as e:
                _failed_until[key] = now + FAILED_COOLDOWN_S
                raise RuntimeError(str(e)[:300])
            _clients[key] = client
        return client


def close_all() -> None:
    """Close every cached server. Never raises."""
    try:
        with _clients_lock:
            clients = list(_clients.values())
            _clients.clear()
            _failed_until.clear()
        for c in clients:
            try:
                c.close()
            except Exception:
                pass
    except Exception:
        pass


atexit.register(close_all)


def load_servers() -> list[dict]:
    """Configured servers from the global config file. Never raises."""
    try:
        from .config import Config

        return [dict(s) for s in (Config.load().mcp_servers or ())]
    except Exception:
        return []


def mcp_schema_extra() -> list[dict]:
    """OpenAI-function schema entries for live servers. Slow/dead servers are
    skipped (with cooldown) so prompt building never stalls. Never raises."""
    out: list[dict] = []
    for spec in load_servers():
        try:
            tools = get_client(spec).list_tools()
        except Exception:
            continue
        for t in tools:
            desc = f"[mcp:{spec['name']}] " + (t.get("description") or t["name"])
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": f"{PREFIX}{spec['name']}__{t['name']}",
                        "description": desc[:200],
                        "parameters": t.get("inputSchema") or {"type": "object"},
                    },
                }
            )
    return out


def mcp_tool_names() -> set[str]:
    """Approval-gated names of live server tools. Never raises."""
    try:
        return {
            str(e.get("function", {}).get("name", ""))
            for e in mcp_schema_extra()
            if str(e.get("function", {}).get("name", ""))
        }
    except Exception:
        return set()


def dispatch_mcp_tool(name: str, args: dict | None) -> str:
    """Route mcp__<server>__<tool> to tools/call. Returns text; never raises."""
    try:
        rest = name[len(PREFIX) :] if name.startswith(PREFIX) else ""
        server, sep, tool = rest.partition("__")
        if not sep or not tool:
            return f"Error: unknown tool '{name}'"
        spec = next((s for s in load_servers() if s.get("name") == server), None)
        if spec is None:
            return f"Error: unknown mcp server '{server}'"
        try:
            client = get_client(spec)
        except Exception as e:
            return f"Error: mcp server '{server}' unavailable: {e}"
        return client.call_tool(tool, args if isinstance(args, dict) else {})
    except Exception as e:
        return f"Error: mcp dispatch failed: {e}"
