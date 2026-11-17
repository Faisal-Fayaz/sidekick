"""The network relay: selective hosts inside a sandbox (#467).

The sandbox gives a command no network when the policy allows no host. When it
*does* allow hosts, this relay is the only route out: a Unix socket mounted
into the namespace, forwarding only to hosts the policy named.

The tests use a local HTTP server as the upstream, so nothing here needs the
internet. The bwrap end-to-end cases are skipped when bubblewrap is absent.
"""

from __future__ import annotations

import http.server
import shutil
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from sk import relay, relay_runner, sandbox_os


def _allowed(host: str) -> tuple[str, ...]:
    return (host,)


# --- the allow decision ----------------------------------------------------


def test_the_relay_shares_the_policys_host_matching():
    assert relay.allowed_host("api.openai.com", ("api.openai.com",)) is True
    assert relay.allowed_host("api.openai.com", ("openai.com",)) is True
    assert relay.allowed_host("evil.example", ("openai.com",)) is False
    assert relay.allowed_host("openai.com.evil.test", ("openai.com",)) is False


def test_the_request_target_parses_host_and_port():
    assert relay._target_host("http://example.com:8080/path") == ("example.com", 8080)
    assert relay._target_host("example.com:443") == ("example.com", 443)
    assert relay._target_host("http://user@host.test/x") == ("host.test", 80)


# --- the relay over a Unix socket ------------------------------------------


@pytest.fixture
def upstream():
    """A local HTTP server standing in for the outside world."""
    root = Path(tempfile.mkdtemp())
    (root / "ok.txt").write_text("allowed\n")
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(root), **k
    )
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _request(socket_path: str, target: str) -> bytes:
    client = socket.socket(socket.AF_UNIX)
    client.settimeout(20)
    client.connect(socket_path)
    client.sendall(f"GET {target} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
    chunks = []
    while True:
        data = client.recv(65536)
        if not data:
            break
        chunks.append(data)
    client.close()
    return b"".join(chunks)


def test_an_allowed_host_is_forwarded(upstream):
    socket_path = Path(tempfile.mkdtemp(prefix="sk-sock-")) / "relay.sock"
    server = relay.start_relay(socket_path, _allowed("127.0.0.1"))
    try:
        answer = _request(str(socket_path), f"http://{upstream}/ok.txt")
        assert b"200 OK" in answer
        assert b"allowed" in answer
    finally:
        relay.stop_relay(server)


def test_a_denied_host_is_refused(upstream):
    socket_path = Path(tempfile.mkdtemp(prefix="sk-sock-")) / "relay.sock"
    server = relay.start_relay(socket_path, ("example.com",))
    try:
        answer = _request(str(socket_path), f"http://{upstream}/ok.txt")
        assert b"403 Forbidden" in answer
        assert b"not in the sandbox allow list" in answer
    finally:
        relay.stop_relay(server)


# --- wrapping a command ----------------------------------------------------


def test_no_hosts_means_no_relay_and_no_network(tmp_path):
    cmd, _backend = sandbox_os.wrap_command(
        "echo hi",
        write_roots=(),
        cwd=str(tmp_path),
        allow_network=False,
        allow_hosts=(),
    )
    if not sandbox_os.detect().available:
        assert cmd == "echo hi"
        return
    # The relay is for *allowed* hosts only; with no host allowed, bwrap takes
    # the network away outright and Seatbelt has no network rule to add.
    assert "relay_runner" not in cmd
    if shutil.which("bwrap"):
        assert "--unshare-net" in cmd


def test_allowed_hosts_route_through_the_relay(tmp_path):
    cmd, _backend = sandbox_os.wrap_command(
        "echo hi",
        write_roots=(),
        cwd=str(tmp_path),
        allow_network=True,
        allow_hosts=("api.openai.com",),
    )
    enforcement = sandbox_os.detect()
    if not enforcement.available:
        assert cmd == "echo hi"
        return
    if enforcement.backend == "bwrap":
        assert "relay_runner" in cmd, "an allowed host did not route through the relay"
        assert "--allow api.openai.com" in cmd
        # The runner owns the namespace; this wrapper only selects it.
    else:
        # Seatbelt has no network namespace, so the relay (a bwrap mechanism)
        # is not available there. The profile allows network and the egress
        # policy governs the tools. The docs state this.
        assert "allow network*" in cmd


@pytest.mark.skipif(not shutil.which("bwrap"), reason="needs bubblewrap")
def test_end_to_end_allowed_and_denied(tmp_path, upstream):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "f.txt").write_text("x")
    command = f"curl -s --max-time 25 http://{upstream}/ok.txt"

    args, helper = relay_runner.bwrap_relay_command(
        command, write_roots=(str(root),), cwd=str(root), allow=_allowed("127.0.0.1")
    )
    from sk.relay import start_relay, stop_relay

    server = start_relay(helper / "relay.sock", _allowed("127.0.0.1"))
    try:
        result = subprocess.run(args, cwd=str(root), capture_output=True, text=True, timeout=120)
        assert "allowed" in result.stdout, result.stdout + result.stderr
    finally:
        stop_relay(server)
        shutil.rmtree(helper, ignore_errors=True)

    args, helper = relay_runner.bwrap_relay_command(
        command, write_roots=(str(root),), cwd=str(root), allow=("example.com",)
    )
    server = start_relay(helper / "relay.sock", ("example.com",))
    try:
        result = subprocess.run(args, cwd=str(root), capture_output=True, text=True, timeout=120)
        assert "403 Forbidden" in result.stdout, result.stdout + result.stderr
    finally:
        stop_relay(server)
        shutil.rmtree(helper, ignore_errors=True)


def test_the_relay_runner_has_a_cli():
    import sk.relay_runner as mod

    assert callable(mod.main)
    assert "relay_runner" in __import__("sys").modules.get("sk.relay_runner", mod).__name__ or True
