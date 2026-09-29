"""Checkpoints + /rewind tests (fixes #103): snapshot/rewind primitives,
_gated_dispatch hook, slash wiring. Fully offline."""

import json
import os

import sk.agent as agent
import sk.checkpoints as cp
import sk.slash as slash
import sk.store as store
from sk.config import Config


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    return {
        "session": "test",
        "cfg": Config(
            provider="ollama",
            model="t",
            base_url="http://x/v1",
            api_key="x",
            max_steps=1,
            temperature=0.0,
        ),
        "state": {"yolo": False, "readonly": False},
    }


def _cpfile(tmp_path):
    return tmp_path / ".sidekick" / "checkpoints" / "s.json"


def test_snapshot_and_rewind_edit(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    f = tmp_path / "f.txt"
    f.write_text("v1")
    assert cp.snapshot_before("s", "edit_file", {"path": str(f)}) == 1
    f.write_text("v2")
    out = cp.rewind("s")
    assert f.read_text() == "v1"
    assert "#1" in out and "restored original bytes" in out


def test_snapshot_write_new_then_rewind_removes(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    f = tmp_path / "new.txt"
    assert cp.snapshot_before("s", "write_file", {"path": str(f)}) == 1
    f.write_text("created by agent")
    out = cp.rewind("s")
    assert not f.exists()
    assert "removed created file" in out


def test_snapshot_delete_then_rewind_recreates(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    f = tmp_path / "gone.txt"
    f.write_text("precious")
    cp.snapshot_before("s", "delete_file", {"path": str(f)})
    f.unlink()
    out = cp.rewind("s")
    assert f.read_text() == "precious"
    assert "restored original bytes" in out


def test_binary_safe(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    f = tmp_path / "b.bin"
    blob = os.urandom(4096)
    f.write_bytes(blob)
    cp.snapshot_before("s", "edit_file", {"path": str(f)})
    f.write_bytes(b"clobbered")
    cp.rewind("s")
    assert f.read_bytes() == blob


def test_cap_prunes_oldest(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    f = tmp_path / "f.txt"
    f.write_text("v0")
    for i in range(1, cp.MAX_CHECKPOINTS + 2):
        f.write_text(f"v{i}")
        assert cp.snapshot_before("s", "edit_file", {"path": str(f)}) == i
    recs = json.loads(_cpfile(tmp_path).read_text())
    assert len(recs) == cp.MAX_CHECKPOINTS
    assert [r["n"] for r in recs] == list(range(2, cp.MAX_CHECKPOINTS + 2))
    assert "have #2..#21" in cp.rewind("s", 1)


def test_snapshot_skips(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    f = tmp_path / "f.txt"
    f.write_text("x")
    assert cp.snapshot_before("s", "exec", {"cmd": "ls"}) is None
    assert cp.snapshot_before("", "edit_file", {"path": str(f)}) is None
    assert cp.snapshot_before("s", "edit_file", {"path": ""}) is None
    assert cp.snapshot_before("s", "edit_file", {"path": str(tmp_path)}) is None
    big = tmp_path / "big.bin"
    big.write_bytes(b"0" * (cp.MAX_FILE_BYTES + 1))
    assert cp.snapshot_before("s", "write_file", {"path": str(big)}) is None
    assert "no checkpoints" in cp.rewind("s")


def test_rewind_empty_and_bad_n(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    assert "no checkpoints" in cp.rewind("nosuch")
    f = tmp_path / "f.txt"
    f.write_text("a")
    cp.snapshot_before("s", "edit_file", {"path": str(f)})
    out = cp.rewind("s", 9)
    assert out.startswith("Error: no checkpoint #9")


def test_rewind_corrupt_bytes_errors(tmp_path, monkeypatch):
    """Undecodable snapshot bytes surface as Error:, never raise."""
    _iso(tmp_path, monkeypatch)
    p = _cpfile(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    recs = [
        {
            "n": 1,
            "ts": 1.0,
            "tool": "write_file",
            "target": str(tmp_path / "f.txt"),
            "original_b64": "a",  # invalid padding: b64decode raises
        }
    ]
    p.write_text(json.dumps(recs))
    out = cp.rewind("s")
    assert out.startswith("Error: rewind failed")


def test_corrupt_file_treated_empty(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    p = _cpfile(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json")
    assert "no checkpoints" in cp.rewind("s")


def test_gated_dispatch_snapshots_approved_edits(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    f = tmp_path / "live.txt"
    f.write_text("before")
    result, approved = agent._gated_dispatch(
        "edit_file",
        {"path": str(f), "old_string": "before", "new_string": "after"},
        approve=lambda n, a: True,
        session="s",
    )
    assert approved is True and f.read_text() == "after"
    out = cp.rewind("s")
    assert "#1" in out and f.read_text() == "before"
    assert result and "Error" not in result[:20]


def test_gated_dispatch_denied_leaves_no_checkpoint(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    f = tmp_path / "live.txt"
    f.write_text("before")
    result, approved = agent._gated_dispatch(
        "edit_file",
        {"path": str(f), "old_string": "before", "new_string": "after"},
        approve=lambda n, a: False,
        session="s",
    )
    assert approved is False and f.read_text() == "before"
    assert "no checkpoints" in cp.rewind("s")


def test_slash_rewind(tmp_path, monkeypatch):
    c = _iso(tmp_path, monkeypatch)
    out = slash.handle("/rewind abc", session="s", cfg=c["cfg"], state=c["state"])
    assert out.handled and "usage" in out.text
    out = slash.handle("/rewind", session="s", cfg=c["cfg"], state=c["state"])
    assert out.handled and "no checkpoints" in out.text
    f = tmp_path / "f.txt"
    f.write_text("v1")
    cp.snapshot_before("s", "edit_file", {"path": str(f)})
    f.write_text("v2")
    out = slash.handle("/rewind", session="s", cfg=c["cfg"], state=c["state"])
    assert out.handled and f.read_text() == "v1" and "#1" in out.text
    out = slash.handle("/help", session="s", cfg=c["cfg"], state=c["state"])
    assert "/rewind" in out.text
