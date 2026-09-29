"""Export tests: full-session Markdown with interleaved tool events. Fully offline."""

import sk.store as store


def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")


def _seed():
    store.save_message("s1", "user", "list tmp please")
    store.save_message("s1", "assistant", "here: a, b")
    store.log_tool_run("s1", "list_dir", "list_dir|path=/tmp", True, "ollama", "localhost", True)
    store.log_tool_run("s1", "shell", "shell|cmd=rm -rf /", False, "groq", "api.groq.com", False)


def test_export_merges_chronologically(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    _seed()
    events = store.export_session("s1")
    assert [e["kind"] for e in events] == ["msg", "msg", "tool", "tool"]
    assert events[0]["role"] == "user"
    text = store.render_transcript("s1", events)
    assert text.startswith("# Session `s1` — 1 turns, 2 tool calls")
    assert "## " in text and "list tmp please" in text and "here: a, b" in text
    assert "`list_dir`" in text and "DENIED" in text
    assert "(local)" in text and "groq@api.groq.com" in text


def test_export_unknown_and_empty(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    assert store.export_session("nosuch") == []
    assert store.export_session("") == []
    assert store.export_session("  ") == []


def test_export_cli_stdout_file_and_errors(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso(tmp_path, monkeypatch)
    _seed()
    res = CliRunner().invoke(app, ["export", "s1"])
    assert res.exit_code == 0, res.output
    assert "# Session `s1`" in res.output and "DENIED" in res.output
    # default session = latest
    res = CliRunner().invoke(app, ["export"])
    assert res.exit_code == 0 and "# Session `s1`" in res.output
    # unknown session errors cleanly
    res = CliRunner().invoke(app, ["export", "nosuch"])
    assert res.exit_code != 0 and "no such session" in res.output
    # --out writes; refuse overwrite without --force
    dest = tmp_path / "t.md"
    res = CliRunner().invoke(app, ["export", "s1", "--out", str(dest)])
    assert res.exit_code == 0 and dest.read_text().startswith("# Session `s1`")
    res = CliRunner().invoke(app, ["export", "s1", "--out", str(dest)])
    assert res.exit_code != 0 and "--force" in res.output
    res = CliRunner().invoke(app, ["export", "s1", "--out", str(dest), "--force"])
    assert res.exit_code == 0
