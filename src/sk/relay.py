"""An allow-listing HTTP/CONNECT relay for the sandbox (#467).

When a policy allows specific hosts, the sandboxed process still has no direct
network: the network namespace has no route except loopback. This relay is the
only way out. It listens on a Unix socket that the sandbox mounts, accepts HTTP
proxy requests (absolute-form GET and CONNECT), and opens an upstream
connection **only** to a host the policy named. A redirect to another host is
not followed by this relay; the client would have to ask again, and the ask
would be refused.

A policy that allows no hosts needs none of this: the sandbox simply has no
network at all.
"""

from __future__ import annotations

import select
import socket
import socketserver
import threading
from pathlib import Path

from .sandbox import _host_allowed


def allowed_host(host: str, allow: tuple[str, ...]) -> bool:
    """One place for the decision, so the relay cannot disagree with the policy."""
    return _host_allowed(str(host or "").strip().lower(), tuple(allow))


def _target_host(target: str) -> tuple[str, int]:
    """The host and port from a proxy request target."""
    text = str(target or "").strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    host = text.split("/", 1)[0].split("?", 1)[0]
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    port = 80
    if ":" in host:
        host, _, raw_port = host.rpartition(":")
        try:
            port = int(raw_port)
        except ValueError:
            port = 80
    return host.lower(), port


class _RelayHandler(socketserver.StreamRequestHandler):
    """One connection: check the target, then tunnel."""

    def handle(self) -> None:
        relay: AllowListRelay = self.server  # type: ignore[assignment]
        try:
            first = self.rfile.readline(8192)
            if not first:
                return
            parts = first.decode("utf-8", "replace").split()
            if len(parts) < 2:
                return
            method, target = parts[0].upper(), parts[1]
            host, port = _target_host(target)
            if not allowed_host(host, relay.allow):
                self._refuse(f"host {host!r} is not in the sandbox allow list")
                return

            # Read the rest of the request headers before opening anything.
            headers: list[bytes] = []
            while True:
                line = self.rfile.readline(8192)
                if not line or line in (b"\r\n", b"\n"):
                    break
                if not line.lower().startswith(b"host:"):
                    headers.append(line)

            try:
                upstream = socket.create_connection((host, port), timeout=relay.timeout)
            except Exception as e:  # noqa: BLE001 - an unreachable upstream is a report
                self._refuse(f"could not reach {host}:{port}: {type(e).__name__}")
                return

            with upstream:
                if method == "CONNECT":
                    self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    self.wfile.flush()
                else:
                    # A proxy client sends the full URL; an origin server wants
                    # just the path. Rewrite it and set Host ourselves, so the
                    # upstream sees an ordinary request.
                    path = target.split("://", 1)[-1].split("/", 1)
                    origin_path = "/" + (path[1] if len(path) > 1 else "")
                    version = parts[2] if len(parts) > 2 else "HTTP/1.1"
                    upstream.sendall(f"{method} {origin_path} {version}\r\n".encode())
                    upstream.sendall(f"Host: {host}:{port}\r\n".encode())
                    for header in headers:
                        upstream.sendall(header)
                    upstream.sendall(b"\r\n")
                _tunnel(self.connection, upstream)
        except Exception:  # noqa: BLE001 - a relay failure closes this connection
            return

    def _refuse(self, reason: str) -> None:
        body = f"403 Forbidden: {reason}\r\n".encode()
        try:
            self.wfile.write(
                b"HTTP/1.1 403 Forbidden\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            self.wfile.flush()
        except Exception:
            pass


def _tunnel(a: socket.socket, b: socket.socket) -> None:
    """Copy bytes both ways until either side closes."""
    sockets = [a, b]
    while sockets:
        readable, _, _ = select.select(sockets, [], [], 60)
        if not readable:
            break
        for sock in readable:
            try:
                data = sock.recv(65536)
            except OSError:
                return
            if not data:
                return
            other = b if sock is a else a
            try:
                other.sendall(data)
            except OSError:
                return


class AllowListRelay(socketserver.ThreadingUnixStreamServer):
    """A Unix-socket HTTP proxy that forwards only to allowed hosts."""

    daemon_threads = True
    allow: tuple[str, ...] = ()
    timeout: float = 20.0

    def __init__(
        self,
        socket_path: str | Path,
        allow: tuple[str, ...],
        *,
        timeout: float = 20.0,
    ):
        self.allow = tuple(allow)
        self.timeout = float(timeout)
        path = str(socket_path)
        # A stale socket from a crashed run would make bind fail.
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass
        super().__init__(path, _RelayHandler)


def start_relay(
    socket_path: str | Path,
    allow: tuple[str, ...],
    *,
    timeout: float = 20.0,
) -> AllowListRelay:
    """Run a relay in a background thread. Returns it, for `.shutdown()`."""
    relay = AllowListRelay(socket_path, allow, timeout=timeout)
    thread = threading.Thread(target=relay.serve_forever, daemon=True)
    thread.start()
    return relay


def stop_relay(relay: AllowListRelay) -> None:
    """Stop the relay and remove its socket."""
    path = str(relay.server_address)
    try:
        relay.shutdown()
    except Exception:
        pass
    try:
        relay.server_close()
    except Exception:
        pass
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass
