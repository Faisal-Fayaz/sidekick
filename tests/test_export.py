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


def test_render_marks_targets_and_empty_shapes():
    """render_transcript marks, empty shapes, and target cap — pure function, no DB."""
    events = [
        {"kind": "msg", "role": "user", "content": "", "ts": 0},
        {
            "kind": "tool",
            "tool": "ok_tool",
            "target": "t",
            "approved": 1,
            "provider": "ollama",
            "host": "localhost",
            "ok": 1,
            "ts": 0,
        },
        {
            "kind": "tool",
            "tool": "fail_tool",
            "target": "",
            "approved": 1,
            "provider": "groq",
            "host": "api.groq.com",
            "ok": 0,
            "ts": 0,
        },
        {
            "kind": "tool",
            "tool": "deny_tool",
            "target": "x" * 300,
            "approved": 0,
            "provider": "",
            "host": "",
            "ok": 0,
            "ts": 0,
        },
    ]
    text = store.render_transcript("sx", events)
    assert text.startswith("# Session `sx` — 1 turns, 3 tool calls")
    assert "(empty)" in text  # blank message body
    assert "--" in text  # zero timestamps
    assert "tool `ok_tool` ✓ (local)" in text
    assert "tool `fail_tool` ✗ failed (groq@api.groq.com)" in text
    assert "(no target)" in text
    assert "tool `deny_tool` DENIED (local)" in text  # empty host = local
    assert ("`" + "x" * 200 + "`") in text and "x" * 201 not in text  # target cap


def test_export_session_isolation_and_counts(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    store.save_message("s1", "user", "hello")
    store.save_message("s2", "user", "other session")
    store.log_tool_run("s2", "shell", "shell|cmd=ls", True, "ollama", "localhost", True)
    s1 = store.export_session("s1")
    assert [e["kind"] for e in s1] == ["msg"]  # no leak from s2
    assert store.render_transcript("s1", s1).startswith("# Session `s1` — 1 turns, 0 tool calls")
    s2 = store.export_session("s2")
    assert [e["kind"] for e in s2] == ["msg", "tool"]
    # tools-only session renders 0 turns
    store.log_tool_run("s3", "read", "read|path=/tmp", True, "", "", True)
    assert store.render_transcript("s3", store.export_session("s3")).startswith(
        "# Session `s3` — 0 turns, 1 tool calls"
    )


def test_export_cli_no_sessions_and_whitespace(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso(tmp_path, monkeypatch)
    res = CliRunner().invoke(app, ["export"])
    assert res.exit_code != 0 and "no sessions yet" in res.output
    _seed()
    res = CliRunner().invoke(app, ["export", "   "])  # whitespace = latest
    assert res.exit_code == 0 and "# Session `s1`" in res.output


def test_export_cli_out_nested_and_unwritable(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _iso(tmp_path, monkeypatch)
    _seed()
    nested = tmp_path / "a" / "b" / "t.md"  # parents auto-created
    res = CliRunner().invoke(app, ["export", "s1", "--out", str(nested)])
    assert res.exit_code == 0 and nested.read_text().startswith("# Session `s1`")
    (tmp_path / "blocker").write_text("x")
    bad = tmp_path / "blocker" / "t.md"  # parent is a file → cannot write
    res = CliRunner().invoke(app, ["export", "s1", "--out", str(bad)])
    assert res.exit_code != 0 and "cannot write" in res.output
