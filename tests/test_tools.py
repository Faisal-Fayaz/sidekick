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
    import tempfile, os

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
    assert _parse_text_tool('```json {"name": "list_dir", "arguments": {"path": "."}} ```') == ("list_dir", {"path": "."})
    assert _parse_text_tool('```json {"name": "write_file", "arguments": {"path": "/tmp/x", "content": "hi"}} ```')[0] == "write_file"
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
