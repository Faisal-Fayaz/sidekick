"""Memory store tests: isolated DB via tmp HOME."""

import sk.store as store


def _isolate(tmp_path, monkeypatch):
    db = tmp_path / "history.db"
    monkeypatch.setattr(store, "DB_PATH", db)
    return db


def test_save_and_recall(monkeypatch, tmp_path):
    _isolate(tmp_path, monkeypatch)
    assert "Remembered" in store.save_memory("prefers fast model llama")
    assert "Remembered" in store.save_memory("works on ecomind voice app")
    hits = store.recall_memories("fast model", limit=5)
    assert any("fast" in h for h in hits)
    hits2 = store.recall_memories("ecomind", limit=5)
    assert any("ecomind" in h for h in hits2)


def test_recall_empty_query_returns_recent(monkeypatch, tmp_path):
    _isolate(tmp_path, monkeypatch)
    store.save_memory("fact one")
    out = store.recall_memories("", limit=5)
    assert out == ["fact one"]


def test_forget(monkeypatch, tmp_path):
    _isolate(tmp_path, monkeypatch)
    store.save_memory("prefers qwen fast")
    assert "1" in store.forget_memory("qwen")
    assert store.recall_memories("qwen") == [] or all(
        "qwen" not in h for h in store.recall_memories("qwen")
    )


def test_tools_dispatch_memory(monkeypatch, tmp_path):
    _isolate(tmp_path, monkeypatch)
    from sk.tools import dispatch_tool

    assert "Remembered" in dispatch_tool("remember", {"content": "likes local llms"})
    out = dispatch_tool("recall", {"query": "local"})
    assert "local" in out.lower()
