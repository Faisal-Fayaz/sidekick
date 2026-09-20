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


def test_backends_available(monkeypatch):
    monkeypatch.setattr(clip.shutil, "which", lambda b: "/usr/bin/xclip" if b == "xclip" else None)
    assert clip.backends_available() == ["xclip"]
    monkeypatch.setattr(clip.shutil, "which", lambda b: None)
    assert clip.backends_available() == []
    assert "xclip" in clip.install_hint()


def test_slash_copy_warns_without_backends(tmp_path, monkeypatch):
    import sk.clip as _c
    import sk.slash as slash
    import sk.store as store
    from sk.config import Config

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(_c, "backends_available", lambda: [])
    monkeypatch.setattr(_c, "copy_text", lambda t: "osc52")
    cfg = Config(model="t", base_url="http://x/v1", api_key="x", max_steps=1, temperature=0.0)
    store.save_message("s", "assistant", "ans")
    out = slash.handle("/copy", session="s", cfg=cfg, state={})
    assert "osc52" in out.text and "xclip" in out.text


def test_slash_copy_lines(tmp_path, monkeypatch):
    import sk.clip as _c
    import sk.slash as slash
    import sk.store as store
    from sk.config import Config

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    seen: list[str] = []
    monkeypatch.setattr(_c, "backends_available", lambda: ["xclip"])
    monkeypatch.setattr(_c, "copy_text", lambda t: seen.append(t) or "xclip")
    cfg = Config(model="t", base_url="http://x/v1", api_key="x", max_steps=1, temperature=0.0)
    store.save_message("s", "assistant", "l1\nl2\nl3\nl4")
    out = slash.handle("/copy lines 2", session="s", cfg=cfg, state={})
    assert seen == ["l3\nl4"] and "2 lines" in out.text
    out = slash.handle("/copy lines", session="s", cfg=cfg, state={})
    assert seen[-1] == "l1\nl2\nl3\nl4"  # default: whole tail
    assert "no answers" in slash.handle("/copy lines 1", session="e", cfg=cfg, state={}).text.lower()


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
