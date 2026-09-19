"""Clipboard + /copy tests: no real clipboard needed."""

import sk.clip as clip


def test_osc52_format():
    seq = clip.osc52_sequence("hi")
    assert seq.startswith("\x1b]52;c;") and seq.endswith("\x07")
    import base64

    assert base64.b64decode(seq[7:-1]).decode() == "hi"


def test_prefers_wl_copy(monkeypatch):
    calls = []
    monkeypatch.setattr(clip.shutil, "which", lambda b: "/usr/bin/wl-copy" if b == "wl-copy" else None)
    monkeypatch.setattr(clip.subprocess, "run", lambda *a, **k: calls.append(a) or type("R", (), {})())
    assert clip.copy_text("hello") == "wl-copy"
    assert calls


def test_falls_through_to_osc52(monkeypatch, capsys):
    monkeypatch.setattr(clip.shutil, "which", lambda b: None)
    assert clip.copy_text("hello") == "osc52"
    assert "52;c;" in capsys.readouterr().out


def test_empty_raises():
    import pytest

    with pytest.raises(ValueError):
        clip.copy_text("")


def test_slash_copy(tmp_path, monkeypatch):
    import sk.slash as slash
    import sk.store as store
    from sk.config import Config

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    cfg = Config(model="t", base_url="http://x/v1", api_key="x", max_steps=1, temperature=0.0)
    assert "no answers" in slash.handle("/copy", session="s", cfg=cfg, state={}).text.lower()
    store.save_message("s", "assistant", "answer one")
    store.save_message("s", "assistant", "answer two")
    import sk.clip as _c

    monkeypatch.setattr(_c, "copy_text", lambda t: "mockclip")  # handler binds at call time
    out = slash.handle("/copy 2", session="s", cfg=cfg, state={})
    assert "copied" in out.text.lower() and "mockclip" in out.text
