"""Tools package: re-exports (split from sk/tools.py, pure move).

All imports below preserve the old `from sk.tools import X` surface.
New code should import from sk.tools.read / .write / .shell / .web / .registry.
"""

from pathlib import Path  # noqa: F401  (compat: tests patch sk.tools.Path.home)

from .read import (
    ALLOWED_BINARIES,
    ALLOWED_GIT,
    BLOCKED_CHARS,
    _check_cmd,
    tool_exec,
    tool_list_dir,
    tool_read_file,
    tool_sysinfo,
)
from .registry import APPROVAL_TOOLS, TOOLS_SCHEMA, approval_tools, dispatch_tool, tools_schema
from .shell import SHELL_BLOCK_PATTERNS, _check_shell, tool_shell
from .web import _html_to_text, _url_blocked, tool_read_url, tool_web_search
from .write import (
    WRITE_BLOCKLIST,
    WRITE_TOOLS,
    _check_write_path,
    tool_delete_file,
    tool_edit_file,
    tool_make_dir,
    tool_write_file,
)

__all__ = [
    "ALLOWED_BINARIES",
    "ALLOWED_GIT",
    "APPROVAL_TOOLS",
    "BLOCKED_CHARS",
    "SHELL_BLOCK_PATTERNS",
    "TOOLS_SCHEMA",
    "WRITE_BLOCKLIST",
    "WRITE_TOOLS",
    "_check_cmd",
    "_check_shell",
    "_check_write_path",
    "_html_to_text",
    "_url_blocked",
    "approval_tools",
    "dispatch_tool",
    "tool_delete_file",
    "tool_edit_file",
    "tool_exec",
    "tool_list_dir",
    "tool_make_dir",
    "tool_read_file",
    "tool_read_url",
    "tool_shell",
    "tool_sysinfo",
    "tool_web_search",
    "tool_write_file",
    "tools_schema",
]
