"""A localhost HTTP surface over the same agent loop, tools, and audit trail.

**Localhost only.** The server binds 127.0.0.1 and refuses anything else: a
service that can run the agent must not be reachable from the network.

**A token, always.** The token is generated at startup, written to the config
directory with 0600, and printed. Without it, a page in the user's browser
could POST to the local port and make the agent act — the same-origin policy
stops it *reading* the token, so it cannot send a valid one.

The turns run through the same loop, the same tools and the same audit ledger
as the CLI. Nothing here is a second implementation.
"""

from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import CONFIG_DIR

TOKEN_PATH = "serve.token"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

# Only a loopback address is a local address. 0.0.0.0 is not: it means every
# interface, which is exactly the mistake this refuses.
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def is_loopback(host: str) -> bool:
    """True when `host` can only be reached from this machine."""
    name = str(host or "").strip().lower()
    if name in LOOPBACK_HOSTS:
        return True
    # 127.0.0.0/8 is all loopback.
    return name.startswith("127.")


def token_path() -> Path:
    return Path(CONFIG_DIR) / TOKEN_PATH


def generate_token() -> str:
    """A fresh token for this run."""
    return secrets.token_urlsafe(32)


def write_token(token: str, path: Path | None = None) -> Path:
    """Write the token with owner-only permissions, and return its path."""
    from .fsperm import PRIVATE_FILE, ensure_private_dir, tighten

    target = Path(path) if path is not None else token_path()
    ensure_private_dir(target.parent)
    target.write_text(token)
    tighten(target, want=PRIVATE_FILE)
    return target


def read_token(path: Path | None = None) -> str:
    """The token from the last run, or ""."""
    target = Path(path) if path is not None else token_path()
    try:
        return target.read_text().strip()
    except Exception:
        return ""


class _Handler(BaseHTTPRequestHandler):
    """The routes: /health, /turn, /tool."""

    server_version = "sidekick"
    token = ""
    read_only = False

    # --- plumbing ---

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - BaseHTTPRequestHandler API
        """Quiet by default: the TUI and the daemon own the terminal."""

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "") or ""
        expected = f"Bearer {self.token}"
        # Compare in constant time: the token is a credential.
        return secrets.compare_digest(header.strip(), expected)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            data = json.loads(raw.decode("utf-8")) if raw else {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    # --- routes ---

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") not in ("/health", ""):
            self._send(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        import sk

        self._send(200, {"ok": True, "version": sk.__version__})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        route = self.path.rstrip("/")
        data = self._body()
        if route == "/turn":
            self._send(200, self._turn(data))
        elif route == "/tool":
            self._send(200, self._tool(data))
        else:
            self._send(404, {"error": "not found"})

    def _agent(self, session: str):
        """The agent this handler serves.

        In read-only mode the approval gate denies the tools that change
        anything: the local API can read the repository, but it cannot edit it
        unless the operator said so.
        """
        from .sdk import Agent

        if not self.read_only:
            return Agent(session=session)

        def approve(name: str, args: dict) -> bool:
            from .tools import approval_tools

            return name not in approval_tools()

        return Agent(session=session, approve=approve)

    def _turn(self, data: dict) -> dict:
        message = str(data.get("message") or "").strip()
        if not message:
            return {"error": "message is required"}
        session = str(data.get("session") or "http").strip() or "http"
        try:
            agent = self._agent(session)
            answer = agent.turn(message)
        except Exception as e:  # noqa: BLE001 - a failed turn is a report
            return {"error": str(e)[:400]}
        return {"answer": answer, "session": session}

    def _tool(self, data: dict) -> dict:
        name = str(data.get("name") or "").strip()
        if not name:
            return {"error": "name is required"}
        args = data.get("args")
        session = str(data.get("session") or "http").strip() or "http"
        try:
            agent = self._agent(session)
            result = agent.tool(name, args if isinstance(args, dict) else {})
        except Exception as e:  # noqa: BLE001 - a failed call is a report
            return {"error": str(e)[:400]}
        return {"result": result, "session": session}


def make_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    token: str = "",
    read_only: bool = False,
) -> ThreadingHTTPServer:
    """Build the server. Refuses anything that is not a loopback address."""
    if not is_loopback(host):
        raise ValueError(
            f"refusing to bind {host!r}: the local API serves this machine only, never 0.0.0.0"
        )
    handler = type("_BoundHandler", (_Handler,), {"token": token, "read_only": bool(read_only)})
    return ThreadingHTTPServer((host, int(port)), handler)


def serve(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    token: str = "",
    read_only: bool = False,
    block: bool = True,
) -> ThreadingHTTPServer:
    """Start the server. With `block=False` it returns immediately."""
    server = make_server(host=host, port=port, token=token, read_only=read_only)
    if not block:
        return server
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return server
