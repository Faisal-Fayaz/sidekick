"""Push-to-talk voice input: arecord capture + local faster-whisper STT.

No hard deps: arecord ships with the OS, faster-whisper installs on demand
(`sk talk` asks first, ~800MB + ~75MB tiny model). Everything stays local.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

STT_MODEL_DEFAULT = "tiny"
INSTALL_HINT = "run `sk talk --install` (downloads ~800MB deps + ~75MB model, stays offline after)"


def check_mic() -> tuple[bool, str]:
    if shutil.which("arecord") is None:
        return (False, "arecord not found — `sudo apt install alsa-utils`")
    try:
        r = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=10)
        if "List of CAPTURE" in (r.stdout or ""):
            return (True, "mic ready")
        return (False, "no capture devices listed")
    except Exception as e:
        return (False, f"arecord probe failed: {e}")


def start_recording(out_wav: str, device: str = "default", rate: int = 16000) -> subprocess.Popen:
    """Start arecord in the background. Caller stops it (Enter) via stop_recording.

    arecord's stderr goes to <out_wav>.stderr.log so real failures (busy
    device, bad format) survive instead of vanishing into DEVNULL.
    """
    log_path = out_wav + ".stderr.log"
    log_fh = open(log_path, "wb")
    try:
        proc = subprocess.Popen(
            ["arecord", "-D", device, "-r", str(rate), "-f", "S16_LE", "-c", "1", "-t", "wav", out_wav],
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
        lines = [l for l in data.splitlines() if l.strip()]
        return "\n".join(lines[-4:])[-n:]
    except Exception:
        return ""


def stop_recording(proc: subprocess.Popen, timeout: int = 5, wav_path: str = "") -> str | None:
    """Stop recorder gracefully. Returns None on success, error string otherwise.

    NOTE: SIGTERM makes arecord exit 1 even on success ("Aborted by signal
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
        return f"arecord exited {proc.returncode}: {detail}"
    return f"arecord exited {proc.returncode}"


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


def transcribe(wav_path: str, model_size: str = STT_MODEL_DEFAULT) -> str:
    """Transcribe wav with local faster-whisper (int8 CPU). Downloads model once."""
    import os

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
        raise RuntimeError("heard only silence — speak louder/closer, or run `sk mic-test` to check levels")
    return text


def mic_level(duration: int = 3, device: str = "default") -> dict:
    """Record briefly and measure peak/RMS. Returns dict with verdict + hint."""
    import audioop
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
        peak = audioop.max(frames, width)
        rms = audioop.rms(frames, width)
    except Exception as e:
        return {"ok": False, "verdict": "unreadable", "hint": f"audio parse failed: {e}"}
    full = float(1 << (width * 8 - 1))
    peak_db = 20 * math.log10(max(peak, 1) / full)
    if peak < 50:
        return {"ok": False, "peak": peak, "rms": rms, "peak_db": round(peak_db, 1), "verdict": "silent",
                "hint": "mic hears nothing. Unmute/raise it: `alsamixer` (F4 capture, M unmutes), or try `--device hw:2,0`."}
    if peak_db < -30:
        return {"ok": True, "peak": peak, "rms": rms, "peak_db": round(peak_db, 1), "verdict": "quiet",
                "hint": f"very quiet ({round(peak_db,1)} dB). Move closer or boost gain in alsamixer."}
    return {"ok": True, "peak": peak, "rms": rms, "peak_db": round(peak_db, 1), "verdict": "good",
            "hint": "levels look fine — if words still vanish, try `--stt-model base`."}


def record_once(duration: int, device: str = "default") -> Path:
    """Fixed-duration capture for tests/scripting. Returns wav path."""
    out = Path(tempfile.mkdtemp(prefix="sk-voice-")) / "in.wav"
    subprocess.run(
        ["arecord", "-D", device, "-d", str(duration), "-r", "16000", "-f", "S16_LE", "-c", "1", "-t", "wav", str(out)],
        capture_output=True,
        timeout=duration + 15,
        check=True,
    )
    return out
