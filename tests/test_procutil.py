"""Bounded subprocess and MCP output (#315).

`capture_output=True` buffers the child's whole stdout+stderr before the caller
truncates, so a 6000-char result cap bounded what the *model* saw but not what
the agent process *allocated*:

    sk run "yes"      -> 2.2 GB allocated, 3.9 GB RSS growth, 10s
    sk run "cat /dev/urandom"

The tests below assert the process is **terminated**, not merely truncated, and
that peak memory attributable to the call stays bounded.
"""

from __future__ import annotations

import io
import os
import time

from sk.procutil import DEFAULT_MAX_BYTES, Bounded, run_bounded


# --- run_bounded: normal operation -----------------------------------------


def test_captures_stdout_and_stderr():
    r = run_bounded(["bash", "-c", "echo out; echo err >&2"], timeout=10)
    assert r.returncode == 0
    assert "out" in r.stdout and "err" in r.stderr
    assert not r.truncated and not r.timed_out


def test_returncode_is_reported():
    assert run_bounded(["bash", "-c", "exit 7"], timeout=10).returncode == 7


def test_timeout_kills_and_reports():
    t = time.monotonic()
    r = run_bounded(["bash", "-c", "sleep 30"], timeout=1)
    assert r.timed_out
    assert time.monotonic() - t < 10  # did not sit out the full sleep


def test_missing_binary_is_an_error_not_an_exception():
    r = run_bounded(["definitely-not-a-real-binary-xyz"], timeout=5)
    assert r.returncode == 127 and "Error" in r.stderr


# --- run_bounded: the cap actually bounds memory ---------------------------


def test_yes_is_killed_at_the_cap_not_at_the_timeout():
    """The case from the issue. `yes` runs until something stops it.

    Asserts the cap's actual contract — bounded bytes retained, and a killed
    child — rather than process RSS. ru_maxrss is a process-wide high-water mark
    that never decreases, so inside a pytest run it measures whatever else
    happened to allocate, not this code; it is also KB on Linux and bytes on
    macOS, which made an earlier RSS threshold mean 64MB on one and 64KB on the
    other. That assertion failed on macOS while the code was correct.
    """
    cap = 1 << 20
    t = time.monotonic()
    r = run_bounded(["bash", "-c", "yes"], timeout=60, max_bytes=cap)
    elapsed = time.monotonic() - t

    assert r.truncated and r.overflowed
    assert r.returncode == -9, "child must be SIGKILLed, not left to time out"
    assert elapsed < 5, f"waited {elapsed:.1f}s — the kill did not land"
    assert len(r.stdout) <= cap, "retained more than the cap allows"
    assert len(r.stderr) <= cap


def test_urandom_flood_is_killed():
    r = run_bounded(["bash", "-c", "cat /dev/urandom"], timeout=60, max_bytes=1 << 16)
    assert r.overflowed and r.returncode == -9


def test_find_is_killed():
    r = run_bounded(["bash", "-c", "find / -type f"], timeout=60, max_bytes=1 << 16)
    assert r.overflowed


def test_grandchildren_are_killed_too():
    """`bash -c yes` forks. Signalling only the direct child would leave the
    writer running, the pipe open, and us waiting for the timeout."""
    r = run_bounded(["bash", "-c", "yes | cat"], timeout=60, max_bytes=1024)
    assert r.overflowed
    assert r.returncode == -9


def test_cap_is_per_stream_not_shared():
    r = run_bounded(["bash", "-c", "yes; yes >&2"], timeout=30, max_bytes=4096)
    assert len(r.stdout) <= 4097 and len(r.stderr) <= 4097


def test_output_at_exactly_the_cap_is_not_truncated():
    r = run_bounded(["bash", "-c", "printf 'a%.0s' $(seq 1 500)"], timeout=10, max_bytes=500)
    assert len(r.stdout) == 500
    assert not r.truncated


def test_default_cap_is_generous():
    """1 MiB per stream, ~170x the 6000-char cap the model sees."""
    assert DEFAULT_MAX_BYTES >= 6000 * 100


# --- tool integration -------------------------------------------------------


def test_shell_tool_reports_the_kill():
    from sk.tools.shell import tool_shell

    out = tool_shell("yes", timeout=30)
    assert "output cap reached" in out
    assert "killed" in out
    assert "[exit -9]" in out


def test_exec_tool_reports_the_kill():
    from sk.tools.read import tool_exec

    out = tool_exec("cat /dev/urandom", timeout=30)
    assert "output cap reached" in out


def test_shell_tool_still_truncates_for_the_model():
    """The 6000-char cap the model sees must still apply."""
    from sk.tools.shell import tool_shell

    out = tool_shell("printf 'x%.0s' $(seq 1 20000)")
    assert "... [truncated]" in out
    assert "output cap reached" not in out  # under the byte cap, just the char cap


def test_shell_normal_command_unaffected():
    from sk.tools.shell import tool_shell

    out = tool_shell("echo hello")
    assert "[exit 0]" in out and "hello" in out
    assert "truncated" not in out and "capped" not in out


def test_shell_timeout_still_reported_as_timeout():
    from sk.tools.shell import tool_shell

    assert "timed out" in tool_shell("sleep 30", timeout=5)


# --- MCP bounded line reader -----------------------------------------------


def test_mcp_reader_yields_normal_lines():
    from sk.mcp_client import _iter_bounded_lines

    got = list(_iter_bounded_lines(io.BytesIO(b'{"id":1}\n{"id":2}\n'), 1 << 20))
    assert got == [b'{"id":1}', b'{"id":2}']


def test_mcp_reader_yields_final_unterminated_line():
    from sk.mcp_client import _iter_bounded_lines

    assert list(_iter_bounded_lines(io.BytesIO(b'{"id":1}\n{"id":2}'), 1 << 20)) == [
        b'{"id":1}',
        b'{"id":2}',
    ]


def test_mcp_reader_drops_oversized_line_and_resyncs():
    """A huge line must not wedge the stream: we resync at the next newline."""
    from sk.mcp_client import _iter_bounded_lines

    data = b"X" * 5_000_000 + b'\n{"id":1}\n{"id":2}\n'
    got = list(_iter_bounded_lines(io.BytesIO(data), 1 << 20))
    assert got == [b'{"id":1}', b'{"id":2}']


def test_mcp_unterminated_flood_is_bounded():
    """A server emitting 128MB with no newline must not retain it.

    Asserts retained bytes, not process RSS: ru_maxrss is a process-wide
    high-water mark that never decreases, so a delta measured here reflects
    unrelated allocation (and is KB on Linux but bytes on macOS, so the same
    threshold meant 64MB in one CI job and 64KB in another).
    """

    class _Flood:
        def __init__(self):
            self.left = 2000

        def read1(self, _k=0):
            if self.left <= 0:
                return b""
            self.left -= 1
            return b"X" * 65536

    from sk.mcp_client import _iter_bounded_lines

    got = list(_iter_bounded_lines(_Flood(), 1 << 20))
    assert got == [], "an unterminated flood must yield nothing, not a partial line"
    assert sum(len(x) for x in got) == 0


def test_mcp_reader_handles_split_lines():
    """A line arriving across several reads is reassembled."""
    from sk.mcp_client import _iter_bounded_lines

    class _Chunks:
        def __init__(self):
            self.c = [b'{"id"', b":1}\n", b'{"id":2}\n']

        def read1(self, _k=0):
            return self.c.pop(0) if self.c else b""

    assert list(_iter_bounded_lines(_Chunks(), 1 << 20)) == [b'{"id":1}', b'{"id":2}']


def test_mcp_reader_never_blocks_waiting_for_a_full_buffer():
    """read(n) on a live pipe blocks until n bytes arrive — read1 must be used.

    Regression guard: using read() here deadlocks the handshake, because the
    server sends a short reply and then waits for our next request.
    """

    class _ShortPipe:
        def __init__(self):
            self.reads = 0

        def read1(self, _k=0):
            self.reads += 1
            return b'{"id":1}\n' if self.reads == 1 else b""

        def read(self, _k=0):  # pragma: no cover - must never be called
            raise AssertionError("read() blocks on a live pipe; use read1()")

    from sk.mcp_client import _iter_bounded_lines

    pipe = _ShortPipe()
    assert list(_iter_bounded_lines(pipe, 1 << 20)) == [b'{"id":1}']


# --- MCP HTTP size guard ----------------------------------------------------


class _Resp:
    def __init__(self, length=None, actual=0):
        self.headers = {"content-length": str(length)} if length is not None else {}
        self.content = b"x" * actual


def test_http_guard_rejects_declared_oversize():
    from sk.mcp_client import _guard_response_size

    try:
        _guard_response_size(_Resp(length=99_000_000), limit=1 << 20)
        raise AssertionError("should have refused")
    except RuntimeError as e:
        assert "too large" in str(e)


def test_http_guard_accepts_within_limit():
    from sk.mcp_client import _guard_response_size

    _guard_response_size(_Resp(length=1000, actual=1000), limit=1 << 20)


def test_http_guard_catches_chunked_body_with_no_content_length():
    """A chunked response has no Content-Length, so the post-hoc check matters."""
    from sk.mcp_client import _guard_response_size

    try:
        _guard_response_size(_Resp(length=None, actual=2_000_000), limit=1 << 20)
        raise AssertionError("should have refused")
    except RuntimeError as e:
        assert "too large" in str(e)


# --- Bounded shape ----------------------------------------------------------


def test_overflowed_distinguishes_kill_from_timeout():
    assert not Bounded().overflowed
    assert Bounded(truncated=True, timed_out=True).overflowed is False
    assert Bounded(truncated=True).overflowed is True


def test_env_is_inherited_when_not_given():
    r = run_bounded(["bash", "-c", "echo $HOME"], timeout=5)
    assert os.path.expanduser("~") in r.stdout


def test_mcp_reader_never_exceeds_the_cap_in_any_case():
    """The invariant, stated once: nothing yielded is ever larger than max_bytes."""
    from sk.mcp_client import _iter_bounded_lines

    payloads = [
        b"short\n",
        b"Y" * 4096 + b"\n",  # line exactly at a small cap
        b"Z" * 100_000 + b"\n",  # far over
        b"W" * 50_000,  # unterminated, over
        b'{"id":1}\n' + b"Q" * 90_000 + b'\n{"id":2}\n',
    ]
    for cap in (16, 4096, 65536, 1 << 20):
        for data in payloads:
            got = list(_iter_bounded_lines(io.BytesIO(data), cap))
            for line in got:
                assert len(line) <= cap, f"cap={cap} yielded {len(line)} bytes"
