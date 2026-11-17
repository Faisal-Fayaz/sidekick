"""Run a command in a network namespace with only an allow-listing relay.

The sandbox gives the command no network except loopback. This runner starts a
relay on the host (on a Unix socket inside the project, which the sandbox sees),
starts a small TCP-to-Unix forwarder *inside* the namespace on loopback, and
points `HTTP_PROXY`/`HTTPS_PROXY` at it. A direct connection has no route; a
proxied one reaches only the hosts the policy named.

The helper files live under the project root, so bubblewrap can see them
without a separate bind. They are removed when the command ends.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from .relay import start_relay, stop_relay

# The forwarder runs inside the sandbox. It has no access to the host network:
# it only bridges a loopback TCP listener to the Unix socket the host relay
# listens on.
_FORWARDER = """import socket, subprocess, sys, threading, time

SOCKET = {socket_path!r}
PORT = {port}
READY = {ready!r}

subprocess.run(["ip", "link", "set", "lo", "up"], capture_output=True)


def tunnel(a, b):
    import select
    while True:
        readable, _, _ = select.select([a, b], [], [], 60)
        if not readable:
            break
        for sock in readable:
            try:
                data = sock.recv(65536)
            except OSError:
                return
            if not data:
                return
            try:
                (b if sock is a else a).sendall(data)
            except OSError:
                return


def bridge(conn):
    try:
        upstream = socket.socket(socket.AF_UNIX)
        upstream.connect(SOCKET)
    except Exception:
        conn.close()
        return
    with upstream:
        tunnel(conn, upstream)
    conn.close()


server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", PORT))
server.listen(8)
open(READY, "w").close()


def accept_loop():
    while True:
        conn, _ = server.accept()
        threading.Thread(target=bridge, args=(conn,), daemon=True).start()


threading.Thread(target=accept_loop, daemon=True).start()
while True:
    time.sleep(3600)
"""


def _free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def bwrap_relay_command(
    cmd: str,
    *,
    write_roots: tuple[str, ...] = (),
    cwd: str = "",
    allow: tuple[str, ...] = (),
) -> tuple[list[str], Path]:
    """Build the bwrap argv and the helper directory for a relayed command."""
    workdir = Path(cwd or os.getcwd()).resolve()
    # A system temp dir keeps the Unix socket path short: macOS limits AF_UNIX
    # paths to ~104 bytes and a deep project path does not fit. The directory
    # is bound into the sandbox below.
    helper_dir = Path(tempfile.mkdtemp(prefix="sk-relay-"))
    socket_path = helper_dir / "relay.sock"
    forwarder = helper_dir / "forwarder.py"
    ready = helper_dir / "ready"
    port = _free_port()
    forwarder.write_text(
        _FORWARDER.format(socket_path=str(socket_path), port=port, ready=str(ready))
    )

    args: list[str] = [
        "bwrap",
        "--ro-bind",
        "/",
        "/",
        "--tmpfs",
        "/tmp",
    ]
    for root in list(write_roots) + [str(workdir), str(helper_dir)]:
        args.extend(["--bind", root, root])
    args.extend(
        [
            "--dev-bind",
            "/dev",
            "/dev",
            "--proc",
            "/proc",
            "--unshare-pid",
            "--die-with-parent",
            "--new-session",
            "--unshare-net",
        ]
    )
    proxy = f"http://127.0.0.1:{port}"
    # Wait for the forwarder to bind before running the command: starting it
    # in the background and racing the command makes the first connection
    # fail intermittently.
    wrapper = (
        f"python3 {str(forwarder)!r} & FWD=$!; "
        f"for _ in $(seq 1 100); do [ -f {str(ready)!r} ] && break; sleep 0.05; done; "
        f"{cmd}; RC=$?; kill $FWD 2>/dev/null || true; exit $RC"
    )
    args.extend(
        [
            "env",
            f"HTTP_PROXY={proxy}",
            f"HTTPS_PROXY={proxy}",
            f"http_proxy={proxy}",
            f"https_proxy={proxy}",
            "NO_PROXY=",
            "no_proxy=",
            "bash",
            "-c",
            wrapper,
        ]
    )
    return args, helper_dir


def run_relayed(
    cmd: str,
    *,
    write_roots: tuple[str, ...] = (),
    cwd: str = "",
    allow: tuple[str, ...] = (),
) -> tuple[int, str]:
    """Run a command with network access only through the allow-listing relay."""
    args, helper_dir = bwrap_relay_command(cmd, write_roots=write_roots, cwd=cwd, allow=allow)
    socket_path = helper_dir / "relay.sock"
    relay = None
    try:
        relay = start_relay(socket_path, tuple(allow))
        result = subprocess.run(args, cwd=cwd or None, capture_output=True, text=True, timeout=900)
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    finally:
        if relay is not None:
            stop_relay(relay)
        shutil.rmtree(helper_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: `python -m sk.relay_runner --allow HOST -- CMD`."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", default="")
    parser.add_argument("--root", action="append", default=[])
    parser.add_argument("--allow", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command_parts = args.command[1:] if args.command and args.command[0] == "--" else args.command
    cmd = " ".join(command_parts)
    if not cmd.strip():
        print("no command to run", file=sys.stderr)
        return 2
    code, output = run_relayed(
        cmd,
        write_roots=tuple(args.root),
        cwd=args.cwd,
        allow=tuple(args.allow),
    )
    sys.stdout.write(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())


def shell_command(
    cmd: str,
    *,
    write_roots: tuple[str, ...] = (),
    cwd: str = "",
    allow: tuple[str, ...] = (),
) -> str:
    """The shell string that runs `cmd` behind the relay."""
    import shlex

    parts = [sys.executable, "-m", "sk.relay_runner"]
    if cwd:
        parts.extend(["--cwd", cwd])
    for root in write_roots:
        parts.extend(["--root", root])
    for host in allow:
        parts.extend(["--allow", host])
    parts.extend(["--", cmd])
    return " ".join(shlex.quote(part) for part in parts)
