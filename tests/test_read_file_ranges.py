"""#399: read_file line-range slices, validation, caps, and guards."""

from __future__ import annotations

import pytest

from sk.tools import dispatch_tool, tool_read_file
from sk.tools.read import MAX_RANGE_LINES, MAX_READ_BYTES
from sk.tools.registry import TOOLS_SCHEMA

SAMPLE = "alpha\nbeta\ngamma\ndelta\n"


def _sample(tmp_path, text: str = SAMPLE):
    p = tmp_path / "sample.txt"
    p.write_text(text)
    return p


def test_default_read_keeps_raw_output_and_truncation(tmp_path):
    p = _sample(tmp_path)
    assert tool_read_file(str(p)) == SAMPLE
    assert tool_read_file(str(p), max_chars=5) == "alpha\n... [truncated 18 chars]"


def test_exact_range_is_numbered_and_reports_line_count(tmp_path):
    p = _sample(tmp_path)
    out = tool_read_file(str(p), start_line=2, end_line=3)
    assert out == f"{p.resolve()}: lines 2-3 of 4 (2 lines)\n2: beta\n3: gamma"
    assert tool_read_file(str(p), 2, 3) == out


def test_start_only_reads_to_eof(tmp_path):
    p = _sample(tmp_path)
    out = tool_read_file(str(p), start_line=3)
    assert "lines 3-4 of 4 (2 lines)" in out
    assert "3: gamma" in out and "4: delta" in out
    assert "2: beta" not in out


def test_end_only_reads_from_first_line(tmp_path):
    p = _sample(tmp_path)
    out = tool_read_file(str(p), end_line=2)
    assert "lines 1-2 of 4 (2 lines)" in out
    assert "1: alpha" in out and "2: beta" in out
    assert "3: gamma" not in out


def test_first_and_last_line_are_reachable(tmp_path):
    p = _sample(tmp_path)
    assert "1: alpha" in tool_read_file(str(p), start_line=1, end_line=1)
    assert "4: delta" in tool_read_file(str(p), start_line=4, end_line=4)


def test_end_line_past_eof_clamps_with_metadata(tmp_path):
    p = _sample(tmp_path)
    out = tool_read_file(str(p), start_line=2, end_line=99)
    assert "lines 2-4 of 4 (3 lines; end_line 99 clamped to 4)" in out
    assert "4: delta" in out
    assert "5:" not in out


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"start_line": 0}, "start_line must be >= 1"),
        ({"start_line": -2}, "start_line must be >= 1"),
        ({"start_line": 3, "end_line": 2}, "before start_line"),
        ({"start_line": 99}, "beyond end of file"),
        ({"end_line": 0}, "end_line must be >= 1"),
        ({"start_line": "nope"}, "start_line must be an integer"),
        ({"end_line": "nope"}, "end_line must be an integer"),
        ({"start_line": 1, "max_chars": 0}, "max_chars must be positive"),
        ({"start_line": 1, "max_chars": "nope"}, "max_chars must be an integer"),
    ],
)
def test_invalid_ranges_are_rejected(tmp_path, kwargs, needle):
    p = _sample(tmp_path)
    assert needle in tool_read_file(str(p), **kwargs)


def test_huge_range_caps_lines_and_reports_omissions(tmp_path):
    text = "\n".join(f"line {i}" for i in range(1, MAX_RANGE_LINES + 6))
    p = _sample(tmp_path, text)
    out = tool_read_file(str(p), start_line=1, end_line=1_000_000, max_chars=1_000_000)
    assert f"line cap {MAX_RANGE_LINES} reached" in out
    assert f"{MAX_RANGE_LINES}: line {MAX_RANGE_LINES}" in out
    assert f"{MAX_RANGE_LINES + 1}: line {MAX_RANGE_LINES + 1}" not in out
    assert "truncated before line" in out
    assert "lines not shown]" in out


def test_char_cap_keeps_whole_lines_and_reports_omissions(tmp_path):
    text = "\n".join(f"line-{i:02d}" for i in range(1, 11))
    p = _sample(tmp_path, text)
    out = tool_read_file(str(p), start_line=1, end_line=10, max_chars=40)
    assert "char cap 40 reached" in out
    assert "1: line-01" in out
    assert "10: line-10" not in out
    assert "truncated before line" in out


def test_binary_file_refused_for_default_and_range_reads(tmp_path):
    p = tmp_path / "blob.bin"
    p.write_bytes(b"PK\x03\x04\x00\x00secret")
    for kwargs in ({}, {"start_line": 1, "end_line": 1}):
        out = tool_read_file(str(p), **kwargs)
        assert "binary" in out.lower()
        assert "secret" not in out


def test_oversized_file_refused_before_range_read(tmp_path):
    p = tmp_path / "huge.txt"
    p.write_bytes(b"x" * (MAX_READ_BYTES + 1))
    out = tool_read_file(str(p), start_line=1, end_line=1)
    assert "too large" in out
    assert str(MAX_READ_BYTES + 1) in out


def test_range_reads_keep_sensitive_path_guard(tmp_path, monkeypatch):
    import sk.tools as tools

    monkeypatch.setattr(tools.Path, "home", classmethod(lambda cls: tmp_path))
    key = tmp_path / ".ssh" / "id_rsa"
    key.parent.mkdir()
    key.write_text("SECRET-KEY-MATERIAL")
    out = tool_read_file(str(key), start_line=1, end_line=1)
    assert "blocked" in out.lower()
    assert "SECRET-KEY-MATERIAL" not in out


def test_dispatch_forwards_range_and_max_chars(tmp_path):
    p = _sample(tmp_path)
    out = dispatch_tool("read_file", {"path": str(p), "start_line": "2", "end_line": "3"})
    assert "lines 2-3 of 4" in out
    assert "2: beta" in out and "3: gamma" in out
    assert dispatch_tool("read_file", {"path": str(p), "max_chars": 5}) == (
        "alpha\n... [truncated 18 chars]"
    )


def test_schema_advertises_optional_range_fields():
    entry = next(e for e in TOOLS_SCHEMA if e["function"]["name"] == "read_file")
    params = entry["function"]["parameters"]
    props = params["properties"]
    assert params["required"] == ["path"]
    assert {"start_line", "end_line", "max_chars"} <= set(props)
    assert props["start_line"]["type"] == "integer"
    assert props["end_line"]["type"] == "integer"
    assert props["max_chars"]["type"] == "integer"
    assert "line" in entry["function"]["description"].lower()
