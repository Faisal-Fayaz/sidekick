"""Todo store tests: isolated DB."""

import sk.store as store


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def test_add_list_done(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    assert "Added" in store.add_todo("clean disk")
    rows = store.list_todos()
    assert len(rows) == 1 and rows[0][1] == "clean disk"
    tid = rows[0][0]
    assert "Done" in store.complete_todo(tid)
    assert store.list_todos() == []


def test_done_missing(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    assert "not found" in store.complete_todo(999)


def test_dispatch_todos(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    from sk.tools import dispatch_tool

    assert "Added" in dispatch_tool("todo_add", {"text": "x"})
    assert "#" in dispatch_tool("todo_list", {})
    assert "Done" in dispatch_tool("todo_done", {"id": 1})
