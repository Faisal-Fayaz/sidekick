"""Slash dispatcher tests: fully offline (isolated DB + isolated config)."""

import sk.config as config_mod
import sk.slash as slash
import sk.store as store
from sk.config import Config


def _ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    # isolate config file: /model saves must never touch ~/.sidekick/config.toml
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    cfg = Config(model="llama3.2:3b", base_url="http://x/v1", api_key="x", max_steps=1, temperature=0.0)
    return {"session": "test", "cfg": cfg, "state": {"yolo": False}}


def test_config_file_untouched(tmp_path, monkeypatch):
    from pathlib import Path

    real = Path.home() / ".sidekick" / "config.toml"
    before = real.read_bytes() if real.exists() else None
    c = _ctx(tmp_path, monkeypatch)
    slash.handle("/model smart", session=c["session"], cfg=c["cfg"], state=c["state"])
    after = real.read_bytes() if real.exists() else None
    assert before == after
    assert (tmp_path / ".sidekick" / "config.toml").exists()


def test_passthrough(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    assert slash.handle("hello", session=c["session"], cfg=c["cfg"], state=c["state"]).handled is False


def test_help_and_unknown(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    assert "/model" in slash.handle("/help", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    assert "unknown" in slash.handle("/nope", session=c["session"], cfg=c["cfg"], state=c["state"]).text.lower()


def test_model_switch(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    out = slash.handle("/model fast", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert c["cfg"].model == "llama3.2:3b" and "llama" in out.text
    out = slash.handle("/model smart", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert "qwen2.5-coder" in c["cfg"].model


def test_yolo_toggle(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    slash.handle("/yolo", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert c["state"]["yolo"] is True
    slash.handle("/confirm", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert c["state"]["yolo"] is False


def test_memory_roundtrip(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    assert "Remembered" in slash.handle("/remember likes rust", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    assert "rust" in slash.handle("/recall rust", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    assert "rust" in slash.handle("/memories", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    assert "Forgot" in slash.handle("/forget rust", session=c["session"], cfg=c["cfg"], state=c["state"]).text


def test_todo_flow_and_clear(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    assert "Added" in slash.handle("/todo add clean disk", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    assert "clean disk" in slash.handle("/todo list", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    assert "Done" in slash.handle("/todo done 1", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    store.save_message("test", "user", "hi")
    out = slash.handle("/clear", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.clear_view and out.switch_session.startswith("test-")
    assert out.switch_session != "test"
    assert store.get_history("test") != []  # old session kept, not wiped


def test_oops_empty_and_quit(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    assert "Clean shell" in slash.handle("/oops", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    assert slash.handle("/quit", session=c["session"], cfg=c["cfg"], state=c["state"]).quit is True
