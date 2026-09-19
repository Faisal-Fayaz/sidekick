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
    """Start arecord in the background. Caller stops it (Enter) via proc.terminate()."""
    return subprocess.Popen(
        ["arecord", "-D", device, "-r", str(rate), "-f", "S16_LE", "-c", "1", "-t", "wav", out_wav],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_recording(proc: subprocess.Popen, timeout: int = 5) -> str | None:
    """Stop recorder. Returns None on success, error string otherwise."""
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return "recorder would not stop, killed"
    except Exception as e:
        return f"stop failed: {e}"
    if proc.returncode not in (0, None, -15):
        return f"arecord exited {proc.returncode}"
    return None


def ensure_stt() -> tuple[bool, str]:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return (False, f"faster-whisper not installed. {INSTALL_HINT}")
    return (True, "stt ready")


_model_cache: dict[str, object] = {}


def transcribe(wav_path: str, model_size: str = STT_MODEL_DEFAULT) -> str:
    """Transcribe wav with local faster-whisper (int8 CPU). Downloads model once."""
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
        raise RuntimeError("heard nothing — speak closer / check mic level")
    return text


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
