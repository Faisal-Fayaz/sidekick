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


def test_add_todo_rejects_blank_and_overlong(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    assert "Empty" in store.add_todo("   ")
    assert "Too long" in store.add_todo("x" * 501)
    assert store.list_todos(open_only=False) == []


def test_list_todos_can_include_completed(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    store.add_todo("first")
    store.add_todo("second")
    store.complete_todo(1)
    assert [row[1] for row in store.list_todos()] == ["second"]
    assert [(row[1], row[2]) for row in store.list_todos(open_only=False)] == [
        ("first", 1),
        ("second", 0),
    ]


def test_clear_todos_only_removes_completed(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    store.add_todo("done")
    store.add_todo("open")
    store.complete_todo(1)
    assert "Cleared 1" in store.clear_todos()
    assert [row[1] for row in store.list_todos(open_only=False)] == ["open"]
