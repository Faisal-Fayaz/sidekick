"""Fast unit tests: no Ollama needed."""

from sk.agent import _auto_local_context, _parse_text_tool
from sk.tools import (
    tool_edit_file,
    tool_exec,
    tool_list_dir,
    tool_read_file,
    tool_sysinfo,
    tool_write_file,
)


def test_list_dir_tmp():
    out = tool_list_dir("/tmp")
    assert "/tmp" in out


def test_write_read_edit_roundtrip(tmp_path):
    f = tmp_path / "demo.txt"
    assert "Wrote" in tool_write_file(str(f), "hello")
    assert "hello" in tool_read_file(str(f))
    assert "Edited" in tool_edit_file(str(f), "hello", "hi")
    assert "hi" in tool_read_file(str(f))


def test_write_blocklist():
    assert "blocked" in tool_write_file("~/.ssh/evil", "x").lower()
    assert "blocked" in tool_write_file("/etc/evil", "x").lower()


def test_edit_unique():
    import tempfile
    import os

    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".txt") as tf:
        tf.write("aaa aaa")
        name = tf.name
    try:
        out = tool_edit_file(name, "aaa", "b")
        assert "2x" in out or "unique" in out.lower()
        assert "not found" in tool_edit_file(name, "zzz-nope", "y").lower()
    finally:
        os.unlink(name)


def test_exec_allow_and_block():
    assert "exit 0" in tool_exec("pwd")
    assert "Blocked" in tool_exec("rm -rf /") or "not in allowlist" in tool_exec("rm -rf /")


def test_sysinfo_has_sections():
    out = tool_sysinfo()
    assert "CPU:" in out and "RAM:" in out and "OLLAMA" in out


def test_parse_text_tool():
    assert _parse_text_tool('```json {"name": "list_dir", "arguments": {"path": "."}} ```') == (
        "list_dir",
        {"path": "."},
    )
    assert (
        _parse_text_tool(
            '```json {"name": "write_file", "arguments": {"path": "/tmp/x", "content": "hi"}} ```'
        )[0]
        == "write_file"
    )
    assert _parse_text_tool("just a normal answer") is None


def test_auto_context_injects(tmp_path, monkeypatch):
    """Grounding actually injects, for both spellings it claims to handle.

    Previously this asserted only `isinstance(..., str)`, which passes whatever
    the function returns, and carried the author's home directory in a comment
    (#321). Both spellings are exercised here against a monkeypatched HOME, so
    the expectation does not depend on who runs the suite.
    """
    import sk.agent as agent_mod

    home = tmp_path / "home"
    d = home / "proj"
    d.mkdir(parents=True)
    (d / "README.md").write_text("hello-scope")
    (d / "package.json").write_text('{"name":"scope-pkg"}')
    monkeypatch.setattr(agent_mod.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))

    # ~/proj — tilde form
    ctx = _auto_local_context("check ~/proj please")
    assert "proj" in ctx, ctx
    assert "hello-scope" in ctx or "README" in ctx, ctx

    # $HOME/proj — absolute form, which the function matches separately
    ctx2 = _auto_local_context(f"check {home}/proj please")
    assert "proj" in ctx2, ctx2

    # no path mentioned -> nothing injected
    assert _auto_local_context("just a normal question") == ""


def test_make_dir_roundtrip(tmp_path, monkeypatch):
    import sk.tools as _t

    monkeypatch.setattr(_t.Path, "home", classmethod(lambda cls: tmp_path))
    from sk.tools import dispatch_tool, tool_make_dir

    d = tmp_path / "a" / "b"
    assert "Created" in tool_make_dir(str(d)) and d.is_dir()
    assert "Created" in tool_make_dir(str(d))  # idempotent
    assert "blocked" in tool_make_dir("/etc/sk-evil").lower()
    assert "Created" in dispatch_tool("make_dir", {"path": str(tmp_path / "c")})
    assert (tmp_path / "c").is_dir()


def test_make_dir_needs_approval():
    from sk.agent import _gated_dispatch

    out, ok = _gated_dispatch("make_dir", {"path": "/tmp/x"}, approve=lambda n, a: False)
    assert ok is False and "Denied" in out


def test_shell_blocks_catastrophic():
    from sk.tools import dispatch_tool, tool_shell

    for bad in (
        "rm -rf /",
        "rm -rf /*",
        "sudo rm -rf ~",
        "mkfs.ext4 /dev/sda1",
        "dd if=x of=/dev/sda",
        ":(){ :|:& };:",
        "echo hi > /dev/sda",
    ):
        out = tool_shell(bad)
        assert "blocked" in out.lower(), bad
    assert "hello-shell" in tool_shell("echo hello-shell")
    assert "ok" in dispatch_tool("shell", {"cmd": "echo ok"})


def test_shell_timeout_flag():
    from sk.tools import dispatch_tool

    assert "timed out" in dispatch_tool("shell", {"cmd": "sleep 30", "timeout": 1})


def test_delete_file_roundtrip(tmp_path, monkeypatch):
    import sk.tools as _t

    monkeypatch.setattr(_t.Path, "home", classmethod(lambda cls: tmp_path))
    from sk.tools import dispatch_tool, tool_delete_file

    f = tmp_path / "gone.txt"
    f.write_text("x")
    assert "Deleted file" in tool_delete_file(str(f)) and not f.exists()
    assert "does not exist" in tool_delete_file(str(f))
    d = tmp_path / "emptyd"
    d.mkdir()
    assert "empty dir" in tool_delete_file(str(d))
    nd = tmp_path / "fulld"
    nd.mkdir()
    (nd / "x").write_text("x")
    assert "non-empty" in tool_delete_file(str(nd))
    assert "blocked" in tool_delete_file("/etc/sk-evil").lower()
    assert "Deleted" in dispatch_tool(
        "delete_file", {"path": str(tmp_path / "g.txt")}
    ) or "does not exist" in dispatch_tool("delete_file", {"path": str(tmp_path / "g.txt")})


def test_shell_delete_need_approval():
    from sk.agent import _gated_dispatch

    out, ok = _gated_dispatch("shell", {"cmd": "echo hi"}, approve=lambda n, a: False)
    assert ok is False and "Denied" in out
    out, ok = _gated_dispatch("delete_file", {"path": "/tmp/x"}, approve=lambda n, a: False)
    assert ok is False


def test_missing_required_gate():
    from sk.tools import dispatch_tool, missing_required

    assert missing_required("write_file", {}) == ["path", "content"]
    assert missing_required("write_file", {"path": "/tmp/x"}) == ["content"]
    assert missing_required("write_file", {"path": "/tmp/x", "content": ""}) == ["content"]
    assert missing_required("sysinfo", {}) == []
    assert missing_required("nosuchtool", {}) == []
    out = dispatch_tool("write_file", {})
    assert "missing required params" in out and "path" in out and "content" in out
    # no-arg tools still dispatch
    assert "does not exist" in dispatch_tool(
        "list_dir", {"path": "/nope-xyz"}
    ) or "Error" in dispatch_tool("list_dir", {})


def test_gated_dispatch_refuses_empty_before_approval(tmp_path, monkeypatch):
    import sk.store as store
    from sk.agent import _gated_dispatch

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    prompted: list = []
    out, ok = _gated_dispatch(
        "write_file", {}, approve=lambda n, a: prompted.append((n, a)) or True, session="t"
    )
    assert ok is False and "missing required params" in out
    assert prompted == []  # doomed calls never prompt
    rows = store.list_tool_runs("t")
    assert any(r["tool"] == "write_file" and not r["approved"] for r in rows)


def test_missing_message_steers_chunking():
    from sk.tools.registry import missing_message

    plain = missing_message("shell", ["cmd"])
    assert "missing required params" in plain and "skeleton" not in plain
    guided = missing_message("write_file", ["path", "content"])
    assert "missing required params" in guided
    assert "skeleton" in guided and "edit_file" in guided


def test_circuit_breaker_trips_on_repeats(tmp_path, monkeypatch):
    import sk.agent as agent
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(agent, "_fail_counts", {})
    args = {"path": "~/.ssh/evil", "content": "hi"}
    for _ in range(3):
        # approved (ok True) but failed execution: blocklisted path
        out, ok = agent._gated_dispatch("write_file", args, approve=lambda n, a: True, session="t")
        assert ok is True and "blocked" in out
    out, ok = agent._gated_dispatch("write_file", args, approve=lambda n, a: True, session="t")
    assert ok is False and "Stopped" in out and "3 times" in out


def test_circuit_breaker_resets_on_success_and_arg_change(tmp_path, monkeypatch):
    import sk.agent as agent
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(agent, "_fail_counts", {})
    bad = {"path": "~/.ssh/evil", "content": "hi"}
    for _ in range(3):
        agent._gated_dispatch("write_file", bad, approve=lambda n, a: True, session="t")
    # different args = different key: no trip (fails on its own merits)
    out, ok = agent._gated_dispatch(
        "write_file",
        {"path": "~/.gnupg/evil", "content": "hi"},
        approve=lambda n, a: True,
        session="t",
    )
    assert "Stopped" not in out and "blocked" in out
    # a success anywhere resets the session counters
    agent._record_tool_outcome("t", "write_file", "write_file|path=~/.ssh/evil", False)
    out, ok = agent._gated_dispatch("write_file", bad, approve=lambda n, a: True, session="t")
    assert "Stopped" not in out


def test_write_verification_catches_short_writes(tmp_path):
    from sk.tools.write import _verify_write

    f = tmp_path / "full.txt"
    f.write_text("hello world")
    assert _verify_write(f, "hello world") is None
    problem = _verify_write(f, "hello world plus much more content here")
    assert problem is not None and "verification failed" in problem
    assert _verify_write(tmp_path / "missing.txt", "x") is not None


def test_write_file_reports_verified_bytes(tmp_path):
    from sk.tools import tool_write_file

    f = tmp_path / "sized.txt"
    out = tool_write_file(str(f), "héllo wörld")
    assert "Wrote" in out and f.stat().st_size == len("héllo wörld".encode())


def test_system_prompt_has_edit_first_doctrine():
    from sk.agent import SYSTEM_PROMPT

    assert "EDIT FIRST" in SYSTEM_PROMPT
    assert "skeleton" in SYSTEM_PROMPT
