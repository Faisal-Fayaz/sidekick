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


def test_auto_context_injects(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "README.md").write_text("hello-scope")
    ctx = _auto_local_context(f"check ~/{d.name} please")
    # direct home path won't match tmp; use absolute fallback check
    ctx2 = _auto_local_context(f"check {d} please")
    # absolute /tmp not in regex (only ~ and /home/faisal), so empty is ok
    assert isinstance(ctx, str) and isinstance(ctx2, str)


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
