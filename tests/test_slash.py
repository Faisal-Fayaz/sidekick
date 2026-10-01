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
    """/model writes the *isolated* config, never the user's real one.

    Previously read `Path.home() / ".sidekick" / "config.toml"` — the live user
    file. On a machine with no ~/.sidekick it silently passed as `None == None`,
    and on a machine with one it depended on real local state (#321).
    """
    import sk.config as config_mod

    # a decoy at the real HOME: if anything escapes the fixture it shows up here
    decoy = tmp_path / "real-home" / ".sidekick" / "config.toml"
    decoy.parent.mkdir(parents=True)
    decoy.write_text("SENTINEL = 'untouched'")
    monkeypatch.setenv("HOME", str(tmp_path / "real-home"))

    c = _ctx(tmp_path, monkeypatch)
    slash.handle("/model smart", session=c["session"], cfg=c["cfg"], state=c["state"])

    assert decoy.read_text() == "SENTINEL = 'untouched'", "wrote to the real HOME"
    assert config_mod.CONFIG_PATH.exists(), "did not write the isolated config"
    assert config_mod.CONFIG_PATH != decoy


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


def test_research_delegates_readonly_capped(monkeypatch):
    """#233: delegate runs read-only, capped, history untouched."""
    import sk.agent as agent
    import sk.slash as slash
    from sk.config import Config

    seen = {}

    def fake_run(user_msg, history, cfg, **k):
        seen["msg"] = user_msg
        seen["history"] = history
        seen["read_only"] = k.get("read_only")
        seen["max_steps"] = cfg.max_steps
        assert k.get("approve")("write_file", {"path": "x"}) is False
        return "digest: three findings"

    monkeypatch.setattr(agent, "run_agent", fake_run)
    cfg = Config(
        provider="ollama", model="m", base_url="", api_key="", max_steps=15, temperature=0.2
    )
    out = slash.handle("/research why is the sky blue", session="s", cfg=cfg, state={"yolo": False})
    assert out.handled is True and out.text == "digest: three findings"
    assert seen["read_only"] is True and seen["max_steps"] == 3
    assert seen["history"] == [] and seen["msg"] == "why is the sky blue"


def test_research_usage_and_never_raises():
    import sk.slash as slash
    from sk.config import Config

    cfg = Config(
        provider="ollama", model="m", base_url="", api_key="", max_steps=5, temperature=0.2
    )
    out = slash.handle("/research   ", session="s", cfg=cfg, state={})
    assert out.handled is True and "usage" in out.text


def test_delegate_never_raises(monkeypatch):
    import sk.agent as agent
    from sk.config import Config

    def _boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(agent, "run_agent", _boom)
    cfg = Config(
        provider="ollama", model="m", base_url="", api_key="", max_steps=5, temperature=0.2
    )
    assert agent.delegate_research("q", cfg, "s").startswith("Error: research delegate failed")
