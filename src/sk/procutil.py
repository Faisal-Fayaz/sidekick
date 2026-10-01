"""Bounded subprocess output (#315).

`subprocess.run(..., capture_output=True)` buffers the child's **entire**
stdout and stderr into memory before the caller truncates. A 6000-char tool
result cap therefore bounds what the model sees but not what the agent process
allocates:

    sk run "yes"              # GBs in the wall-clock window
    sk run "cat /dev/urandom"
    sk run "find / -type f"

The timeout did not help — it caps elapsed time, not bytes.

`run_bounded` reads incrementally into a fixed-size buffer and, when the cap is
hit, kills the child's whole **process group**. The group matters: `bash -c "yes"`
forks, so signalling only the direct child would leave the writer running and the
pipe open, and we would sit there until the timeout.

Two details that are easy to get wrong:

- **We keep draining after the cap.** If the reader stopped, the child would
  block forever on a full pipe and never exit. Reading and discarding is what
  lets the kill actually land and the pipe close.
- **We kill on overflow rather than waiting for the timeout.** A command that
  would emit 10GB should not get to keep running for the remaining 119 seconds
  after we have already decided to discard its output.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass, field

# 1 MiB per stream. ~170x more than the 6000-char tool cap the model sees, so
# nothing legitimate is lost, while peak memory stays bounded at ~2 MiB.
DEFAULT_MAX_BYTES = 1 << 20

_CHUNK = 65536


@dataclass
class Bounded:
    """Result of a bounded run. `stdout`/`stderr` are already truncated."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False  # cap reached; output above is partial
    timed_out: bool = False
    duration: float = 0.0

    @property
    def overflowed(self) -> bool:
        """True when the child was killed *because* it produced too much."""
        return self.truncated and not self.timed_out


@dataclass
class _Sink:
    cap: int
    buf: list[bytes] = field(default_factory=list)
    count: int = 0
    full: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGKILL the child's process group. Never raises."""
    try:
        if proc.poll() is not None:
            return
        # start_new_session=True puts the child in its own group, so pgid == pid.
        # Guard anyway: signalling our own group would be fatal.
        if proc.pid == os.getpid():
            return
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    except Exception:
        pass


def _drain(stream, sink: _Sink, proc: subprocess.Popen) -> None:
    """Read `stream` into `sink` up to the cap, then discard and let the kill land."""
    try:
        while True:
            try:
                chunk = stream.read(_CHUNK)
            except (ValueError, OSError):
                return
            if not chunk:
                return
            if sink.full:
                continue  # keep draining so the child is never blocked on a pipe
            with sink.lock:
                if sink.full:
                    continue
                room = sink.cap - sink.count
                if room > 0:
                    sink.buf.append(chunk[:room])
                    sink.count += len(chunk[:room])
                # Only data *beyond* the cap counts. `count >= cap` would fire on
                # output that exactly fills the buffer and then stops, reporting
                # truncation for a complete result.
                if len(chunk) > room:
                    sink.full = True
                    over = True
                else:
                    over = False
            if over:
                _kill_group(proc)
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _decode(chunks: list[bytes]) -> str:
    return b"".join(chunks).decode("utf-8", errors="replace")


def run_bounded(
    argv: list[str],
    *,
    timeout: float = 30,
    max_bytes: int = DEFAULT_MAX_BYTES,
    cwd: str | os.PathLike | None = None,
    env: dict | None = None,
    stdin_devnull: bool = True,
) -> Bounded:
    """Run `argv`, capturing at most `max_bytes` per stream. Never raises.

    Returns a `Bounded` with `timed_out` / `truncated` set as appropriate. A
    child that outruns the byte cap is killed, not merely cut off.
    """
    import time

    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL if stdin_devnull else None,
            cwd=cwd,
            env=env,
            start_new_session=True,  # own process group, so the kill reaches grandchildren
        )
    except Exception as e:
        return Bounded(returncode=127, stderr=f"Error: {e}", duration=time.monotonic() - started)

    out_sink, err_sink = _Sink(max_bytes), _Sink(max_bytes)
    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, out_sink, proc), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, err_sink, proc), daemon=True),
    ]
    for t in threads:
        t.start()

    timed_out = False
    try:
        proc.wait(timeout=max(0.1, float(timeout)))
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    finally:
        for t in threads:
            t.join(timeout=5)

    returncode = proc.returncode if proc.returncode is not None else -1
    return Bounded(
        returncode=returncode,
        stdout=_decode(out_sink.buf),
        stderr=_decode(err_sink.buf),
        truncated=out_sink.full or err_sink.full,
        timed_out=timed_out,
        duration=time.monotonic() - started,
    )
