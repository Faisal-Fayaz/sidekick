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
    cfg = Config(
        model="llama3.2:3b", base_url="http://x/v1", api_key="x", max_steps=1, temperature=0.0
    )
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
    assert (
        slash.handle("hello", session=c["session"], cfg=c["cfg"], state=c["state"]).handled is False
    )


def test_help_and_unknown(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    assert (
        "/model" in slash.handle("/help", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    )
    assert (
        "unknown"
        in slash.handle("/nope", session=c["session"], cfg=c["cfg"], state=c["state"]).text.lower()
    )


def test_model_switch(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    out = slash.handle("/model fast", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert c["cfg"].model == "llama3.2:3b" and "llama" in out.text
    out = slash.handle("/model smart", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert "qwen2.5-coder" in c["cfg"].model


def test_model_accepts_provider_name(tmp_path, monkeypatch):
    from sk.config import PRESETS

    c = _ctx(tmp_path, monkeypatch)
    out = slash.handle("/model groq", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert c["cfg"].provider == "groq"
    assert c["cfg"].model == PRESETS["groq"]["model"]
    assert "provider → `groq`" in out.text
    out = slash.handle("/model GROQ", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert "provider → `groq`" in out.text


def test_yolo_toggle(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    slash.handle("/yolo", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert c["state"]["yolo"] is True
    slash.handle("/confirm", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert c["state"]["yolo"] is False


def test_memory_roundtrip(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    assert (
        "Remembered"
        in slash.handle(
            "/remember likes rust", session=c["session"], cfg=c["cfg"], state=c["state"]
        ).text
    )
    assert (
        "rust"
        in slash.handle("/recall rust", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    )
    assert (
        "rust"
        in slash.handle("/memories", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    )
    assert (
        "Forgot"
        in slash.handle("/forget rust", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    )


def test_todo_flow_and_clear(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    assert (
        "Added"
        in slash.handle(
            "/todo add clean disk", session=c["session"], cfg=c["cfg"], state=c["state"]
        ).text
    )
    assert (
        "clean disk"
        in slash.handle("/todo list", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    )
    assert (
        "Done"
        in slash.handle("/todo done 1", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    )
    store.save_message("test", "user", "hi")
    out = slash.handle("/clear", session=c["session"], cfg=c["cfg"], state=c["state"])
    assert out.clear_view and out.switch_session.startswith("test-")
    assert out.switch_session != "test"
    assert store.get_history("test") != []  # old session kept, not wiped


def test_oops_empty_and_quit(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    assert (
        "Clean shell"
        in slash.handle("/oops", session=c["session"], cfg=c["cfg"], state=c["state"]).text
    )
    assert slash.handle("/quit", session=c["session"], cfg=c["cfg"], state=c["state"]).quit is True


def test_fork_flow(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    store.save_message("chat-1", "user", "first")
    store.save_message("chat-1", "assistant", "second")
    store.save_message("chat-1", "user", "third")
    out = slash.handle("/fork 2", session="chat-1", cfg=c["cfg"], state=c["state"])
    assert out.switch_session and out.switch_session != "chat-1"
    assert "2 messages" in out.text
    assert [m["content"] for m in store.get_history(out.switch_session, limit=100)] == [
        "first",
        "second",
    ]
    out = slash.handle("/fork", session="chat-1", cfg=c["cfg"], state=c["state"])
    assert out.switch_session
    assert len(store.get_history(out.switch_session, limit=100)) == 3


def test_fork_errors(tmp_path, monkeypatch):
    c = _ctx(tmp_path, monkeypatch)
    out = slash.handle("/fork", session="empty", cfg=c["cfg"], state=c["state"])
    assert out.switch_session == "" and "nothing to fork" in out.text
    store.save_message("chat-1", "user", "only")
    out = slash.handle("/fork xyz", session="chat-1", cfg=c["cfg"], state=c["state"])
    assert out.switch_session == "" and "usage" in out.text.lower()
    out = slash.handle("/fork 9", session="chat-1", cfg=c["cfg"], state=c["state"])
    assert out.switch_session == "" and "only 1 messages" in out.text
