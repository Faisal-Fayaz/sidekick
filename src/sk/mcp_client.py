"""MCP client: consume external MCP servers as agent tools.

Transports: local stdio servers (`command`) and Streamable HTTP (`url`).
JSON-RPC 2.0, protocol 2024-11-05. Spawning is per-process cached; failures
cool down briefly so a dead server can never stall prompt building. Entry
points used by the agent never raise.
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
from urllib.parse import urljoin

PROTOCOL_VERSION = "2024-11-05"
PREFIX = "mcp__"
FAILED_COOLDOWN_S = 60.0


def _version() -> str:
    try:
        from sk import __version__

        return str(__version__)
    except Exception:
        return "0.0"


def _init_params() -> dict:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "sidekick", "version": _version()},
    }


def _unwrap_response(msg: object, rid: int) -> dict:
    """result dict from a JSON-RPC response, id-checked. Raises RuntimeError."""
    if not isinstance(msg, dict):
        raise RuntimeError("bad response")
    if "id" in msg and msg.get("id") != rid:
        raise RuntimeError("response id mismatch")
    err = msg.get("error")
    if err is not None:
        detail = err.get("message", err) if isinstance(err, dict) else err
        raise RuntimeError(str(detail)[:300])
    result = msg.get("result", {})
    return result if isinstance(result, dict) else {}


def _normalize_tools(result: dict) -> list[dict]:
    """[{name, description, inputSchema}] from a tools/list result."""
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


def _content_text(result: dict, name: str, tool: str) -> str:
    """Text rendering of a tools/call result (or an 'Error: ...' string)."""
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


# --- subprocess environment allowlist ---------------------------------------
# An MCP stdio server is arbitrary code. Handing it the whole parent environment
# gives it every credential the user has in their shell: provider API keys,
# cloud tokens, GITHUB_TOKEN (#302). The reference implementation filters to a
# fixed allowlist plus explicit config for the same reason.
_ENV_ALLOW = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
)


def _safe_env(explicit: dict | None = None, patterns: tuple | list = ()) -> dict[str, str]:
    """Environment for an MCP subprocess: an allowlist, plus explicit entries.

    Never raises. `explicit` comes from the user's own config, so it is applied
    last and wins. Glob patterns (e.g. `AWS_*`) are opt-in per server via the
    `inherit_env` list, so granting access is a visible act rather than a
    side effect of spawning a server.
    """
    out: dict[str, str] = {}
    try:
        for key in _ENV_ALLOW:
            val = os.environ.get(key)
            if val is not None:
                out[key] = val
        for key, val in os.environ.items():
            if key.startswith("XDG_") and val:
                out[key] = val
    except Exception:
        pass
    try:
        for pat in patterns or ():
            from fnmatch import fnmatch

            for key, val in os.environ.items():
                if fnmatch(key, str(pat)):
                    out[key] = val
    except Exception:
        pass
    try:
        for key, val in (explicit or {}).items():
            out[str(key)] = str(val)
    except Exception:
        pass
    return out


class MCPClient:
    """One stdio MCP server process. Call via `with` or connect()/close()."""

    def __init__(
        self,
        name: str,
        command: str,
        args: tuple | list = (),
        env: dict | None = None,
        timeout: float = 30.0,
        trust: str = "untrusted",
        inherit_env: tuple | list = (),
    ):
        self.name = name
        self.command = command
        self.args = [str(a) for a in (args or [])]
        self.env = dict(env or {})
        self.trust = str(trust or "untrusted").strip().lower()
        self.inherit_env = [str(p) for p in (inherit_env or [])]
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, queue.Queue] = {}
        self._wlock = threading.Lock()

    def __enter__(self) -> MCPClient:
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
        full_env = _safe_env(self.env, self.inherit_env)
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
            self._request("initialize", _init_params(), self.timeout)
            self._notify("notifications/initialized")
        except Exception:
            self.close()
            raise

    def list_tools(self) -> list[dict]:
        """Normalized [{name, description, inputSchema}]. Raises RuntimeError."""
        return _normalize_tools(self._request("tools/list", {}, self.timeout))

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
        return _content_text(result, self.name, tool)

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
        return _unwrap_response(msg, rid)

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


_REDIRECT_CODES = (301, 302, 303, 307, 308)


def _origin(url: str) -> tuple[str, str, int]:
    from urllib.parse import urlsplit

    try:
        p = urlsplit(url)
        port = p.port or (443 if p.scheme == "https" else 80)
        return (p.scheme.lower(), (p.hostname or "").lower(), port)
    except Exception:
        return ("", "", 0)


def _same_origin(a: str, b: str) -> bool:
    return _origin(a) == _origin(b)


class MCPHttpClient:
    """Streamable HTTP MCP server. Same interface as MCPClient.

    POSTs JSON-RPC; honors Mcp-Session-Id (re-initializes once on 404) and
    falls back to SSE (text/event-stream) responses. Never raises from
    list/call paths outward — call_tool returns 'Error: …' strings.
    """

    def __init__(
        self,
        name: str,
        url: str,
        headers: dict | None = None,
        timeout: float = 30.0,
    ):
        self.name = name
        self.url = str(url or "").rstrip("/")
        self.headers = dict(headers or {})
        self.timeout = timeout
        self._client = None
        self._session_id: str | None = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def __enter__(self) -> MCPHttpClient:
        self.connect()
        return self

    def __exit__(self, *a: object) -> None:
        self.close()

    def is_alive(self) -> bool:
        return self._client is not None

    def connect(self) -> None:
        """Initialize handshake. Raises RuntimeError."""
        if self._client is not None:
            return
        self.close()
        if not self.url:
            raise RuntimeError("no url configured")
        import httpx

        # follow_redirects=False on purpose: a 302 from the configured MCP
        # host to an attacker host would otherwise replay the Authorization
        # header to whoever the redirect names, which is a free
        # credential-exfiltration primitive (#303). TLS verification stays on.
        self._client = httpx.Client(follow_redirects=False)
        try:
            self._request("initialize", _init_params(), self.timeout)
            try:
                self._post_notification("notifications/initialized")
            except Exception:
                pass
        except Exception:
            self.close()
            raise

    def list_tools(self) -> list[dict]:
        """Normalized [{name, description, inputSchema}]. Raises RuntimeError."""
        return _normalize_tools(self._request("tools/list", {}, self.timeout))

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
        return _content_text(result, self.name, tool)

    def close(self) -> None:
        """Drop the session. Never raises."""
        try:
            client, self._client = self._client, None
            self._session_id = None
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
        except Exception:
            pass

    def _headers(self) -> dict:
        out = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        out.update(self.headers)
        if self._session_id:
            out["Mcp-Session-Id"] = self._session_id
        return out

    def _post_notification(self, method: str) -> None:
        assert self._client is not None
        import httpx

        payload = {"jsonrpc": "2.0", "method": method}
        try:
            self._client.post(
                self.url,
                json=payload,
                headers=self._headers(),
                timeout=httpx.Timeout(10.0, read=10.0, write=10.0, pool=10.0),
            )
        except Exception as e:
            raise RuntimeError(f"notify failed: {e}")

    def _request(self, method: str, params: dict, timeout: float, _retried: bool = False) -> dict:
        """POST + await the response (JSON or SSE). Raises RuntimeError."""
        import httpx

        client = self._client
        if client is None:
            raise RuntimeError("not connected")
        rid = next(self._ids)
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        try:
            with self._lock:
                resp = client.post(
                    self.url,
                    json=payload,
                    headers=self._headers(),
                    timeout=httpx.Timeout(10.0, read=max(1.0, timeout), write=10.0, pool=10.0),
                )
        except httpx.TimeoutException:
            raise RuntimeError(f"timed out after {timeout:g}s")
        except Exception as e:
            raise RuntimeError(f"request failed: {e}")
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        if resp.status_code == 404 and self._session_id and not _retried:
            self._session_id = None
            self._request("initialize", _init_params(), timeout)
            return self._request(method, params, timeout, _retried=True)
        if resp.status_code in _REDIRECT_CODES and not _retried:
            location = resp.headers.get("location", "")
            if not location:
                raise RuntimeError(f"HTTP {resp.status_code} with no Location header")
            nxt = urljoin(self.url, location)
            same_origin = _same_origin(self.url, nxt)
            # Configured headers follow within the origin and never across it.
            saved = dict(self.headers)
            if not same_origin:
                self.headers = {}
            try:
                resp = client.post(
                    nxt,
                    json=payload,
                    headers=self._headers(),
                    timeout=httpx.Timeout(10.0, read=max(1.0, timeout), write=10.0, pool=10.0),
                )
            finally:
                self.headers = saved
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._session_id = sid
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code == 202:
            return {}
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            return self._parse_sse(resp.text, rid)
        try:
            msg = resp.json()
        except Exception:
            raise RuntimeError("bad response")
        return _unwrap_response(msg, rid)

    def _parse_sse(self, text: str, rid: int) -> dict:
        """First id-matching data frame wins, else the last parsed frame."""
        last: object = None
        for line in (text or "").splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                msg = json.loads(data)
            except Exception:
                continue
            if isinstance(msg, dict) and msg.get("id") == rid:
                return _unwrap_response(msg, rid)
            last = msg
        if last is not None:
            return _unwrap_response(last, rid)
        raise RuntimeError("no JSON-RPC response in SSE stream")


_clients: dict[str, MCPClient | MCPHttpClient] = {}
_clients_lock = threading.Lock()
_failed_until: dict[str, float] = {}


def _spec_key(spec: dict) -> str:
    try:
        return (
            spec["name"]
            + "\x00"
            + json.dumps(
                {k: spec.get(k) for k in ("command", "args", "env", "timeout", "url", "headers")},
                sort_keys=True,
            )
        )
    except Exception:
        return spec.get("name", "?") + "\x00?"


def _read_only_hint(tool: dict) -> bool:
    """True only for a literal `readOnlyHint: true`. Fail closed (#303).

    A hint is a server-supplied *claim*, so a lying server can at worst skip
    approval for a tool it claims is read-only. That argument only holds if the
    server is already marked untrusted, which is why the default is untrusted.
    """
    try:
        ann = (tool or {}).get("annotations")
        if not isinstance(ann, dict):
            return False
        return ann.get("readOnlyHint") is True
    except Exception:
        return False


def trust_gate_error(spec: dict, tool_name: str, tools: list[dict]) -> str | None:
    """None when the call may proceed, else why it must not. Never raises."""
    try:
        if str(spec.get("trust", "untrusted") or "untrusted").strip().lower() != "untrusted":
            return None
        for t in tools or []:
            if str(t.get("name", "")) == tool_name and _read_only_hint(t):
                return None
        return (
            f"mcp server {spec.get('name', '?')!r} is untrusted and tool {tool_name!r} "
            "is write-capable (no readOnlyHint=true annotation). Set "
            'trust = "full" in ~/.sidekick/config.toml to allow it unattended.'
        )
    except Exception:
        return "mcp trust check failed closed."


def get_client(spec: dict) -> MCPClient | MCPHttpClient:
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
            if str(spec.get("url", "") or "").strip():
                client = MCPHttpClient(
                    spec["name"],
                    str(spec["url"]),
                    spec.get("headers", {}),
                    float(spec.get("timeout", 30) or 30),
                )
            else:
                client = MCPClient(
                    spec["name"],
                    spec["command"],
                    spec.get("args", ()),
                    spec.get("env", {}),
                    float(spec.get("timeout", 30) or 30),
                    str(spec.get("trust", "untrusted") or "untrusted"),
                    spec.get("inherit_env", ()),
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
        # Trust gate: an untrusted server's write-capable tools must not run
        # unattended. Never transport work before this passes (#303).
        try:
            tools = client.list_tools()
        except Exception:
            tools = []
        blocked = trust_gate_error(spec, tool, tools)
        if blocked:
            return f"Error: {blocked}"
        return client.call_tool(tool, args if isinstance(args, dict) else {})
    except Exception as e:
        return f"Error: mcp dispatch failed: {e}"
