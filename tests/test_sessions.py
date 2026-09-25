"""Session tests: isolated DB. Fresh ids, list/resume/delete flows."""

import sk.store as store


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def test_new_session_id_format():
    sid = store.new_session_id("tui")
    assert sid.startswith("tui-20") and len(sid) > 10
    assert store.new_session_id("chat").startswith("chat-")


def test_list_resume_delete(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    assert store.list_sessions() == []
    store.save_message("tui-20240101-000000", "user", "hello game")
    store.save_message("tui-20240101-000000", "assistant", "hi back")
    store.save_message("tui-20240102-000000", "user", "other thing")
    rows = store.list_sessions()
    assert [r["session"] for r in rows] == ["tui-20240102-000000", "tui-20240101-000000"]
    assert rows[0]["count"] == 1 and rows[1]["count"] == 2
    assert rows[1]["preview"] == "hello game"
    assert store.latest_session() == "tui-20240102-000000"
    assert store.latest_session("tui-20240101") == "tui-20240101-000000"
    assert store.delete_session("tui-20240101-000000") == 2
    assert [r["session"] for r in store.list_sessions()] == ["tui-20240102-000000"]
    assert store.delete_session("nope") == 0


def test_slash_sessions_flow(tmp_path, monkeypatch):
    import sk.slash as slash
    from sk.config import Config

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    cfg = Config(model="t", base_url="http://x/v1", api_key="x", max_steps=1, temperature=0.0)
    assert (
        "no past sessions" in slash.handle("/sessions", session="s", cfg=cfg, state={}).text.lower()
    )
    store.save_message("chat-1", "user", "first topic here")
    out = slash.handle("/sessions", session="chat-1", cfg=cfg, state={})
    assert "chat-1" in out.text and "first topic" in out.text
    out = slash.handle("/resume 5", session="chat-1", cfg=cfg, state={})
    assert "usage" in out.text.lower()
    out = slash.handle("/resume 1", session="other", cfg=cfg, state={})
    assert out.switch_session == "chat-1"
    out = slash.handle("/sessions delete 1", session="x", cfg=cfg, state={})
    assert "deleted" in out.text.lower()
    assert store.list_sessions() == []
    out = slash.handle("/clear", session="chat-9", cfg=cfg, state={})
    assert out.switch_session.startswith("chat-") and out.clear_view is True


async def _pilot_switch_session(monkeypatch, tmp_path):
    import sk.store as _s
    from sk.tui import SidekickTUI as _T

    monkeypatch.setattr(_s, "DB_PATH", tmp_path / "history.db")
    _s.save_message("tui-20200101-000000", "user", "old topic here")
    app = _T()
    async with app.run_test() as pilot:
        assert app.session.startswith("tui-20")  # fresh id per launch
        area = app.query_one("#chat-input")
        area.focus()
        area.text = "/sessions"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "old topic here" in blob
        area.focus()
        area.text = "/resume 1"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app.session == "tui-20200101-000000"
        blob = "\n".join(str(ln) for ln in app.query_one("#chat-log").lines)
        assert "now on" in blob


def test_tui_fresh_session_and_list(monkeypatch, tmp_path):
    _run(_pilot_switch_session(monkeypatch, tmp_path))


def _run(coro):
    import asyncio

    asyncio.run(coro)


def test_fork_exact_prefix_and_original_untouched(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    for role, text in [
        ("user", "one"),
        ("assistant", "two"),
        ("user", "three"),
        ("assistant", "four"),
    ]:
        store.save_message("s", role, text)
    before = store.get_history("s", limit=1000)
    ok, msg = store.fork_session("s", 3, "s-fork")
    assert ok is True and "3 messages" in msg
    forked = store.get_history("s-fork", limit=1000)
    assert [(m["role"], m["content"]) for m in forked] == [
        (m["role"], m["content"]) for m in before[:3]
    ]
    # original byte-identical afterwards
    assert [(m["role"], m["content"]) for m in store.get_history("s", limit=1000)] == [
        (m["role"], m["content"]) for m in before
    ]


def test_fork_divergence_independent(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "shared")
    store.fork_session("s", 1, "f1")
    store.save_message("f1", "assistant", "fork-only reply")
    store.save_message("s", "assistant", "original reply")
    assert [m["content"] for m in store.get_history("f1", limit=100)] == [
        "shared",
        "fork-only reply",
    ]
    assert [m["content"] for m in store.get_history("s", limit=100)] == ["shared", "original reply"]


def test_fork_errors_write_nothing(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    assert store.fork_session("nosuch", 2, "f")[0] is False
    store.save_message("s", "user", "hi")
    for bad in (0, -1, 99, "xyz"):
        ok, msg = store.fork_session("s", bad, "f")
        assert ok is False
    assert all(r["session"] != "f" for r in store.list_sessions())


def test_fork_skips_audit_and_summaries(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "hi")
    store.log_tool_run("s", "exec", "exec|cmd=ls", True, "ollama", "localhost", True)
    store.save_summary("s", "old summary", 1)
    ok, _ = store.fork_session("s", 1, "f")
    assert ok is True
    assert store.list_tool_runs("f") == []
    assert store.get_summary("f") == ("", 0)
    assert store.get_summary("s") == ("old summary", 1)


def test_rapid_forks_never_collide(monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    store.save_message("s", "user", "hi")
    ids = set()
    for _ in range(5):
        ok, msg = store.fork_session("s", 1, store.new_session_id("chat"))
        assert ok is True
        ids.add(msg.split("`")[1])
    assert len(ids) == 5  # same-second forks must not merge histories
    assert store.get_history("s", limit=100) == [{"role": "user", "content": "hi"}]
