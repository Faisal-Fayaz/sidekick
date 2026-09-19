"""Voice tests: all subprocess/model IO mocked. No mic, no downloads."""

import subprocess
import sys
import types

import sk.voice as voice


def test_check_mic_ok(monkeypatch):
    monkeypatch.setattr(voice.shutil, "which", lambda b: "/usr/bin/arecord")

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

    monkeypatch.setattr(voice.subprocess, "Popen", P)
    voice.start_recording("/tmp/x.wav", "hw:2,0")
    argv = seen["argv"]
    assert argv[:3] == ["arecord", "-D", "hw:2,0"]
    assert "-r" in argv and "16000" in argv and argv[-1] == "/tmp/x.wav"


def test_stop_recording():
    class P:
        def __init__(self, rc):
            self._rc = rc
            self.returncode = None

        def terminate(self):
            self.returncode = self._rc

        def wait(self, timeout=None):
            pass

        def kill(self):
            pass

    assert voice.stop_recording(P(0)) is None
    assert "1" in (voice.stop_recording(P(1)) or "")


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
    wav.write_bytes(b"RIFFfake")
    assert voice.transcribe(str(wav)) == "hello world"


def test_transcribe_missing_dep(monkeypatch):
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    import pytest

    with pytest.raises(RuntimeError, match="not installed"):
        voice.transcribe("/tmp/x.wav")


def test_record_once_argv(monkeypatch):
    seen = {}

    def fake_run(*a, **k):
        seen["argv"] = a[0]
        return type("R", (), {})()

    monkeypatch.setattr(voice.subprocess, "run", fake_run)
    out = voice.record_once(3, "hw:1,0")
    assert seen["argv"][:3] == ["arecord", "-D", "hw:1,0"]
    assert "-d" in seen["argv"] and "3" in seen["argv"]
    assert str(out).endswith("in.wav")
