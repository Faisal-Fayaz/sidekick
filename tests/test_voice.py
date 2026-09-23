"""Voice tests: all subprocess/model IO mocked. No mic, no downloads."""

import subprocess
import sys
import types

import sk.voice as voice


def test_check_mic_ok(monkeypatch):
    monkeypatch.setattr(
        voice.shutil, "which", lambda b: "/usr/bin/arecord" if b == "arecord" else None
    )

    class R:
        stdout = "**** List of CAPTURE Hardware Devices ****"

    monkeypatch.setattr(voice.subprocess, "run", lambda *a, **k: R())
    assert voice.check_mic() == (True, "mic ready")


def test_check_mic_missing(monkeypatch):
    monkeypatch.setattr(voice.shutil, "which", lambda b: None)
    ok, msg = voice.check_mic()
    assert ok is False and "alsa-utils" in msg


def test_start_recording_argv(monkeypatch):
    seen = {}

    class P:
        def __init__(self, *a, **k):
            seen["argv"] = a[0]

    monkeypatch.setattr(voice, "detect_recorder", lambda: "arecord")
    monkeypatch.setattr(voice.subprocess, "Popen", P)
    voice.start_recording("/tmp/x.wav", "hw:2,0")
    argv = seen["argv"]
    assert argv[:3] == ["arecord", "-D", "hw:2,0"]
    assert "-r" in argv and "16000" in argv and argv[-1] == "/tmp/x.wav"


def test_start_recording_sox(monkeypatch):
    seen = {}

    class P:
        def __init__(self, *a, **k):
            seen["argv"] = a[0]

    monkeypatch.setattr(voice, "detect_recorder", lambda: "sox")
    monkeypatch.setattr(voice.subprocess, "Popen", P)
    voice.start_recording("/tmp/x.wav", "BuiltInMic")
    argv = seen["argv"]
    assert argv[0] == "sox" and "-t" in argv
    assert argv[-1] == "/tmp/x.wav"


def test_start_recording_ffmpeg(monkeypatch):
    seen = {}

    class P:
        def __init__(self, *a, **k):
            seen["argv"] = a[0]

    monkeypatch.setattr(voice, "detect_recorder", lambda: "ffmpeg")
    monkeypatch.setattr(voice.subprocess, "Popen", P)
    voice.start_recording("/tmp/x.wav", "default")
    argv = seen["argv"]
    assert argv[0] == "ffmpeg" and "-i" in argv
    assert "-ac" in argv and "1" in argv


def test_stop_recording():
    import signal as _s

    class Graceful:
        """SIGINT -> clean exit 0 (fixed behavior)."""

        returncode = None
        signaled = None

        def send_signal(self, sig):
            self.signaled = sig
            self.returncode = 0

        def terminate(self):
            self.returncode = 99

        def wait(self, timeout=None):
            pass

        def kill(self):
            pass

    class Stubborn:
        """SIGINT unsupported -> terminate path."""

        def __init__(self, rc):
            self._rc = rc
            self.returncode = None

        def send_signal(self, sig):
            raise OSError("no sigint")

        def terminate(self):
            self.returncode = self._rc

        def wait(self, timeout=None):
            pass

        def kill(self):
            pass

    g = Graceful()
    assert voice.stop_recording(g) is None and g.signaled == _s.SIGINT
    assert voice.stop_recording(Stubborn(0)) is None
    assert "1" in (voice.stop_recording(Stubborn(1)) or "")


def test_stop_killed_but_valid_wav(tmp_path):
    """SIGTERM rc=1 with good audio is success, not error (the live bug)."""

    class P:
        returncode = 1

        def send_signal(self, sig):
            raise OSError("gone")

        def terminate(self):
            pass

        def wait(self, timeout=None):
            pass

        def kill(self):
            pass

    wav = tmp_path / "in.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 6000)
    assert voice.stop_recording(P(), wav_path=str(wav)) is None


def test_stop_busy_hint(tmp_path, monkeypatch):
    import sk.voice as _v

    class P:
        returncode = 1
        stderr_log = str(tmp_path / "x.wav.stderr.log")

        def send_signal(self, sig):
            pass

        def terminate(self):
            pass

        def wait(self, timeout=None):
            pass

        def kill(self):
            pass

    (tmp_path / "x.wav.stderr.log").write_bytes(b"ALSA lib pcm.c: Device or resource busy\n")
    out = voice.stop_recording(P(), wav_path=str(tmp_path / "x.wav"))
    assert out is not None and "busy" in out.lower()


def test_transcribe_stubbed(monkeypatch, tmp_path):
    mod = types.ModuleType("faster_whisper")

    class Seg:
        def __init__(self, t):
            self.text = t

    class WM:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, path, beam_size=5):
            return ([Seg("hello"), Seg("world")], None)

    mod.WhisperModel = WM
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    wav = tmp_path / "in.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 6000)
    assert voice.transcribe(str(wav)) == "hello world"


def test_transcribe_missing_dep(monkeypatch, tmp_path):
    import sk.config as _cfg

    monkeypatch.setattr(_cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    big = tmp_path / "big.wav"
    big.write_bytes(b"RIFF" + b"\x00" * 6000)
    import pytest

    with pytest.raises(RuntimeError, match="not installed"):
        voice.transcribe(str(big))


def test_record_once_argv(monkeypatch):
    seen = {}

    def fake_run(*a, **k):
        seen["argv"] = a[0]
        return type("R", (), {})()

    monkeypatch.setattr(voice, "detect_recorder", lambda: "arecord")
    monkeypatch.setattr(voice.subprocess, "run", fake_run)
    out = voice.record_once(3, "hw:1,0")
    assert seen["argv"][:3] == ["arecord", "-D", "hw:1,0"]
    assert "-d" in seen["argv"] and "3" in seen["argv"]
    assert str(out).endswith("in.wav")


def test_record_once_sox(monkeypatch):
    seen = {}

    def fake_run(*a, **k):
        seen["argv"] = a[0]
        return type("R", (), {})()

    monkeypatch.setattr(voice, "detect_recorder", lambda: "sox")
    monkeypatch.setattr(voice.subprocess, "run", fake_run)
    voice.record_once(3, "default")
    assert seen["argv"][0] == "sox" and "trim" in seen["argv"] and "3" in seen["argv"]


def test_detect_recorder_prefers_native(monkeypatch):
    monkeypatch.setattr(voice.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        voice.shutil, "which", lambda b: "/usr/bin/" + b if b == "arecord" else None
    )
    assert voice.detect_recorder() == "arecord"

    monkeypatch.setattr(voice.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(voice.shutil, "which", lambda b: "/usr/bin/" + b if b == "sox" else None)
    assert voice.detect_recorder() == "sox"

    monkeypatch.setattr(voice.shutil, "which", lambda b: None)
    assert voice.detect_recorder() is None


def _sine_wav(path, peak_amp, seconds=1):
    import math
    import struct
    import wave

    n = 16000 * seconds
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        frames = b"".join(
            struct.pack("<h", int(peak_amp * math.sin(2 * math.pi * 440 * i / 16000)))
            for i in range(n)
        )
        w.writeframes(frames)


def test_mic_level_silent(monkeypatch, tmp_path):
    wav = tmp_path / "silent.wav"
    _sine_wav(wav, 0)
    monkeypatch.setattr(voice, "record_once", lambda *a, **k: wav)
    res = voice.mic_level(1)
    assert res["verdict"] == "silent" and res["ok"] is False
    assert "alsamixer" in res["hint"]


def test_mic_level_good(monkeypatch, tmp_path):
    wav = tmp_path / "loud.wav"
    _sine_wav(wav, 20000)
    monkeypatch.setattr(voice, "record_once", lambda *a, **k: wav)
    res = voice.mic_level(1)
    assert res["verdict"] == "good" and res["ok"] is True


def test_transcribe_empty_file(tmp_path, monkeypatch):
    import sk.config as _cfg

    monkeypatch.setattr(_cfg, "CONFIG_DIR", tmp_path)
    small = tmp_path / "tiny.wav"
    small.write_bytes(b"RIFF")
    import pytest

    with pytest.raises(RuntimeError, match="nearly empty"):
        voice.transcribe(str(small))


def test_install_cmd_prefers_uv(monkeypatch):
    import sys

    monkeypatch.setattr(voice.shutil, "which", lambda b: "/usr/bin/uv" if b == "uv" else None)
    argv = voice.install_cmd()
    assert argv[:3] == ["uv", "pip", "install"] and sys.executable in argv


def test_install_cmd_pip_fallback(monkeypatch):
    import sys

    monkeypatch.setattr(voice.shutil, "which", lambda b: None)
    assert voice.install_cmd()[:3] == [sys.executable, "-m", "pip"]


def test_install_stt_success(monkeypatch):
    import sys as _sys

    seen = {}

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(*a, **k):
        seen["argv"] = a[0]
        return R()

    monkeypatch.setattr(voice.subprocess, "run", fake_run)
    monkeypatch.setitem(_sys.modules, "faster_whisper", types.ModuleType("faster_whisper"))
    ok, _ = voice.install_stt()
    assert ok is True
    assert seen["argv"][0] in ("uv", _sys.executable)


def test_sanitize_pass_fds():
    """Dupes/negatives/closed fds are dropped; valid ones survive sorted."""
    import os as _os

    probe = _os.open("/dev/null", _os.O_RDONLY)
    stale = _os.dup(probe)
    _os.close(stale)  # numeric slot now closed
    try:
        cleaned = voice._sanitize_pass_fds([probe, probe, -1, stale, 1, probe])
        assert cleaned == (1, probe)
    finally:
        _os.close(probe)


def test_spawn_guard_sanitizes_before_spawn(monkeypatch):
    """spawnv_passfds receives a duplicate -> sanitized tuple -> no ValueError."""
    import multiprocessing.util as _util
    import os as _os

    probe = _os.open("/dev/null", _os.O_RDONLY)
    received: list = []

    def spy(path, args, passfds):
        received.append(tuple(passfds))
        return "mocked"

    try:
        monkeypatch.setattr(_util, "spawnv_passfds", spy)
        voice._spawn_guard_installed = False
        voice._install_spawn_guard()
        # a duplicate fd list would crash _posixsubprocess.fork_exec unguarded
        _util.spawnv_passfds(b"/bin/true", [b"/bin/true"], (probe, probe))
        assert received == [(probe,)]
    finally:
        _os.close(probe)


def test_transcribe_logs_full_traceback(monkeypatch, tmp_path):
    """The swallowed one-liner gets a full traceback in voice-errors.log."""
    import sk.config as _cfg

    monkeypatch.setattr(_cfg, "CONFIG_DIR", tmp_path)
    import pytest

    with pytest.raises(RuntimeError, match="recording file missing"):
        voice.transcribe(str(tmp_path / "nope.wav"))
    log = (tmp_path / "voice-errors.log").read_text()
    assert "=== transcribe @ " in log
    assert "Traceback (most recent call last)" in log
    assert "Recording file missing" in log or "runtime" in log.lower()
