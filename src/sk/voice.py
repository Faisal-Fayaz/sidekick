"""Push-to-talk voice input: cross-platform capture + local faster-whisper STT.

Capture backends: Linux -> arecord (ALSA), macOS -> sox (CoreAudio), fallback
ffmpeg. No hard deps: arecord ships with the OS, sox/ffmpeg via brew; whisper
installs on demand (`sk talk` asks first, ~800MB + ~75MB tiny model).
Everything stays local.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

STT_MODEL_DEFAULT = "tiny"
INSTALL_HINT = "run `sk talk --install` (downloads ~800MB deps + ~75MB model, stays offline after)"


def detect_recorder() -> str | None:
    """First available capture binary, preferring the OS-native one."""
    order = (
        ("sox", "ffmpeg", "arecord")
        if platform.system() == "Darwin"
        else ("arecord", "sox", "ffmpeg")
    )
    for tool in order:
        if shutil.which(tool):
            return tool
    return None


def recorder_install_hint() -> str:
    return (
        "install arecord (`sudo apt install alsa-utils`) on Linux, "
        "or sox/ffmpeg (`brew install sox`) on macOS"
    )


def _capture_start(rec: str, out_wav: str, device: str, rate: int) -> list[str]:
    """Interactive capture argv (stop via SIGINT -> clean finalize)."""
    if rec == "arecord":
        return [
            "arecord",
            "-D",
            device,
            "-r",
            str(rate),
            "-f",
            "S16_LE",
            "-c",
            "1",
            "-t",
            "wav",
            out_wav,
        ]
    if rec == "sox":
        src = ["sox", "-t", "coreaudio", device] if device not in ("", "default") else ["sox", "-d"]
        return src + ["-r", str(rate), "-c", "1", "-b", "16", "-t", "wav", out_wav]
    if platform.system() == "Darwin":
        dev = device if device not in ("", "default") else ":0"
        src = ["ffmpeg", "-f", "avfoundation", "-i", dev]
    else:
        src = ["ffmpeg", "-f", "alsa", "-i", device or "default"]
    return src + ["-ar", str(rate), "-ac", "1", "-y", out_wav]


def _capture_once(rec: str, out_wav: str, device: str, rate: int, duration: int) -> list[str]:
    """Fixed-duration capture argv."""
    if rec == "arecord":
        return [
            "arecord",
            "-D",
            device,
            "-d",
            str(duration),
            "-r",
            str(rate),
            "-f",
            "S16_LE",
            "-c",
            "1",
            "-t",
            "wav",
            out_wav,
        ]
    if rec == "sox":
        src = ["sox", "-t", "coreaudio", device] if device not in ("", "default") else ["sox", "-d"]
        return src + [
            "-r",
            str(rate),
            "-c",
            "1",
            "-b",
            "16",
            "-t",
            "wav",
            out_wav,
            "trim",
            "0",
            str(duration),
        ]
    argv = _capture_start(rec, out_wav, device, rate)
    argv.insert(-1, "-t")
    argv.insert(-1, str(duration))
    return argv


def check_mic() -> tuple[bool, str]:
    rec = detect_recorder()
    if rec is None:
        return (False, f"no audio recorder found — {recorder_install_hint()}")
    if rec != "arecord":
        return (True, f"mic ready ({rec})")  # sox/ffmpeg-able: treat binary presence as ready
    try:
        r = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=10)
        if "List of CAPTURE" in (r.stdout or ""):
            return (True, "mic ready")
        return (False, "no capture devices listed (arecord -l found none)")
    except Exception as e:
        return (False, f"arecord probe failed: {e}")


def start_recording(out_wav: str, device: str = "default", rate: int = 16000) -> subprocess.Popen:
    """Start the native recorder in the background. Caller stops it (Enter) via stop_recording.

    Recorder stderr goes to <out_wav>.stderr.log so real failures (busy
    device, bad format) survive instead of vanishing into DEVNULL.
    """
    rec = detect_recorder()
    if rec is None:
        raise RuntimeError(f"no audio recorder found — {recorder_install_hint()}")
    log_path = out_wav + ".stderr.log"
    log_fh = open(log_path, "wb")
    try:
        proc = subprocess.Popen(
            _capture_start(rec, out_wav, device, rate),
            stdout=subprocess.DEVNULL,
            stderr=log_fh,
        )
    except Exception:
        log_fh.close()
        raise
    proc.stderr_log = log_path  # type: ignore
    return proc


def _stderr_tail(proc: subprocess.Popen, wav_path: str, n: int = 300) -> str:
    path = getattr(proc, "stderr_log", "") or (wav_path + ".stderr.log" if wav_path else "")
    try:
        with open(path, "rb") as f:
            data = f.read()[-2000:].decode(errors="replace")
        lines = [ln for ln in data.splitlines() if ln.strip()]
        return "\n".join(lines[-4:])[-n:]
    except Exception:
        return ""


def stop_recording(proc: subprocess.Popen, timeout: int = 5, wav_path: str = "") -> str | None:
    """Stop recorder gracefully. Returns None on success, error string otherwise.

    NOTE: SIGTERM makes recorders exit 1 even on success ("Aborted by signal
    Terminated"), so we stop with SIGINT (clean finalize, exit 0) and accept
    rc==1 only when the wav file is valid.
    """
    import os
    import signal

    try:
        proc.send_signal(signal.SIGINT)
    except Exception:
        try:
            proc.terminate()
        except Exception as e:
            return f"stop failed: {e}"
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return "recorder would not stop, killed"
    except Exception as e:
        return f"stop failed: {e}"
    if proc.returncode == 0:
        return None
    detail = _stderr_tail(proc, wav_path)
    if "busy" in detail.lower() or "resource busy" in detail.lower():
        return "mic is busy — another app (browser/meet?) holds it. Close it or try another `--device`."
    if wav_path:
        try:
            if os.path.getsize(wav_path) > 5000:
                return None  # killed-but-valid (e.g. SIGTERM rc=1): audio is fine
        except OSError:
            pass
    if detail:
        return f"recorder exited {proc.returncode}: {detail}"
    return f"recorder exited {proc.returncode}"


def ensure_stt() -> tuple[bool, str]:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return (False, f"faster-whisper not installed. {INSTALL_HINT}")
    return (True, "stt ready")


def install_cmd() -> list[str]:
    """Argv to install faster-whisper into the RUNNING env.

    uv-first: `uv tool` envs (which run `sk`) have no pip module, so
    `sys.executable -m pip` fails there. `uv pip install --python <exe>`
    targets the current interpreter regardless of active env.
    """
    import shutil
    import sys

    if shutil.which("uv"):
        return ["uv", "pip", "install", "-q", "--python", sys.executable, "faster-whisper"]
    return [sys.executable, "-m", "pip", "install", "-q", "faster-whisper"]


def install_stt(timeout: int = 900) -> tuple[bool, str]:
    """Install faster-whisper into the running env. Returns (ok, output tail)."""
    try:
        r = subprocess.run(install_cmd(), capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return (False, f"installer crashed: {e}")
    if r.returncode != 0:
        tail = ((r.stderr or "") + (r.stdout or ""))[-500:]
        return (False, f"install failed. Try manually: `uv pip install faster-whisper`\n{tail}")
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return (False, "installed but still not importable — restart the app and retry")
    return (True, "installed")


_model_cache: dict[str, object] = {}

_spawn_guard_installed = False


def _sanitize_pass_fds(fds) -> tuple[int, ...]:
    """Dedupe/sort ``pass_fds`` and drop negatives/stale (closed) descriptors.

    ``multiprocessing`` spawn hands its pipe/tracker fds straight to
    ``_posixsubprocess.fork_exec`` as ``fds_to_keep``. CPython rejects the list
    with ``ValueError: bad value(s) in fds_to_keep`` when it holds a duplicate,
    is unsorted, or names a descriptor that has since been closed (both are
    common when low fd numbers get recycled on macOS). None of those states
    are useful to pass, so the list is cleaned here instead of crashing.
    """
    seen: set[int] = set()
    kept: list[int] = []
    for fd in fds:
        try:
            fd_int = int(fd)
        except (TypeError, ValueError):
            continue
        if fd_int < 0 or fd_int in seen:
            continue
        try:
            os.fstat(fd_int)
        except OSError:
            continue  # already closed: nothing to pass into the child
        seen.add(fd_int)
        kept.append(fd_int)
    return tuple(sorted(kept))


def _install_spawn_guard() -> None:
    """Make multiprocessing spawns immune to ``bad value(s) in fds_to_keep``.

    faster-whisper/ctranslate2 (or any other dependency loaded at transcribe
    time) may start a ``multiprocessing`` child; ``spawnv_passfds`` forwards its
    raw fd list unchanged. Installed once and only around STT work, so the rest
    of the app is untouched.
    """
    global _spawn_guard_installed
    if _spawn_guard_installed:
        return
    _spawn_guard_installed = True
    try:
        import multiprocessing.util as _util
    except Exception:
        return
    original = getattr(_util, "spawnv_passfds", None)
    if original is None:
        return

    def _guarded(path, args, passfds):
        return original(path, args, _sanitize_pass_fds(passfds))

    try:
        _util.spawnv_passfds = _guarded  # type: ignore[method-assign]
    except Exception:
        pass


def _log_traceback(where: str, exc: BaseException) -> None:
    """Append the full traceback so a swallowed one-liner stays debuggable.

    Callers reduce transcribe failures to ``str(exc)`` (the message only), which
    hid the genuine crash site for ``bad value(s) in fds_to_keep``. Best effort;
    never raises.
    """
    import traceback

    try:
        from sk.config import CONFIG_DIR

        path = CONFIG_DIR / "voice-errors.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(f"\n=== {where} @ {time.time():.0f} ===\n")
            f.writelines(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:
        pass


def transcribe(wav_path: str, model_size: str = STT_MODEL_DEFAULT) -> str:
    """Transcribe wav with local faster-whisper (int8 CPU). Downloads model once.

    A spawn guard is installed first so a dependency's multiprocessing spawn
    cannot die on duplicate/recycled descriptors, and any failure is logged with
    its full traceback to ``~/.sidekick/voice-errors.log`` (callers only show the
    one-line message).
    """
    _install_spawn_guard()
    try:
        return _transcribe_impl(wav_path, model_size)
    except Exception as exc:
        _log_traceback("transcribe", exc)
        raise


def _transcribe_impl(wav_path: str, model_size: str) -> str:
    try:
        size = os.path.getsize(wav_path)
    except OSError:
        raise RuntimeError("recording file missing — mic may have failed to start")
    if size < 5000:
        raise RuntimeError("recording is nearly empty — mic may be muted, run `sk mic-test`")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(f"faster-whisper not installed. {INSTALL_HINT}")
    if model_size not in _model_cache:
        _model_cache[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
    model = _model_cache[model_size]
    segments, _ = model.transcribe(wav_path, beam_size=5)  # type: ignore
    text = " ".join(s.text.strip() for s in segments).strip()
    if not text:
        raise RuntimeError(
            "heard only silence — speak louder/closer, or run `sk mic-test` to check levels"
        )
    return text


def _wav_sample_stats(sampwidth: int, frames: bytes) -> tuple[int, int]:
    """(peak, rms) equivalent of audioop.max/rms without stdlib audioop (removed in 3.13)."""
    import struct

    fmt_char = {1: "B", 2: "h", 4: "i"}.get(sampwidth)
    if fmt_char is None:
        raise ValueError(f"unsupported PCM width {sampwidth}")
    # wav is little-endian; endianness must appear BEFORE the count in struct syntax
    endian = "" if sampwidth == 1 else "<"
    n = len(frames) // sampwidth
    if n == 0:
        return (0, 0)
    peak = 0
    sumsq = 0
    step = 8192 * sampwidth
    for off in range(0, len(frames), step):
        block = frames[off : off + step]
        vals = struct.unpack(f"{endian}{len(block) // sampwidth}{fmt_char}", block)
        if sampwidth == 1:
            vals = tuple(v - 128 for v in vals)
        for v in vals:
            a = v if v >= 0 else -v
            if a > peak:
                peak = a
            sumsq += v * v
    rms = int((sumsq / n) ** 0.5) if n else 0
    return peak, rms


def mic_level(duration: int = 3, device: str = "default") -> dict:
    """Record briefly and measure peak/RMS. Returns dict with verdict + hint."""
    import math
    import wave

    out = record_once(duration, device)
    try:
        with wave.open(str(out), "rb") as w:
            frames = w.readframes(w.getnframes())
            width = w.getsampwidth()
    except Exception as e:
        return {"ok": False, "verdict": "unreadable", "hint": f"could not read recording: {e}"}
    try:
        peak, rms = _wav_sample_stats(width, frames)
    except Exception as e:
        return {"ok": False, "verdict": "unreadable", "hint": f"audio parse failed: {e}"}
    full = float(1 << (width * 8 - 1))
    peak_db = 20 * math.log10(max(peak, 1) / full)
    if peak < 50:
        return {
            "ok": False,
            "peak": peak,
            "rms": rms,
            "peak_db": round(peak_db, 1),
            "verdict": "silent",
            "hint": "mic hears nothing. Unmute/raise it: `alsamixer` (F4 capture, M unmutes), or try `--device hw:2,0`.",
        }
    if peak_db < -30:
        return {
            "ok": True,
            "peak": peak,
            "rms": rms,
            "peak_db": round(peak_db, 1),
            "verdict": "quiet",
            "hint": f"very quiet ({round(peak_db, 1)} dB). Move closer or boost gain in alsamixer.",
        }
    return {
        "ok": True,
        "peak": peak,
        "rms": rms,
        "peak_db": round(peak_db, 1),
        "verdict": "good",
        "hint": "levels look fine — if words still vanish, try `--stt-model base`.",
    }


def record_once(duration: int, device: str = "default") -> Path:
    """Fixed-duration capture for tests/scripting. Returns wav path."""
    rec = detect_recorder()
    if rec is None:
        raise RuntimeError(f"no audio recorder found — {recorder_install_hint()}")
    out = Path(tempfile.mkdtemp(prefix="sk-voice-")) / "in.wav"
    subprocess.run(
        _capture_once(rec, str(out), device, 16000, duration),
        capture_output=True,
        timeout=duration + 15,
        check=True,
    )
    return out
