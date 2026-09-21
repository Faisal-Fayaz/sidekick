"""Security regression lock: destructive shell, write blocklist, SSRF, exec allowlist.

Consolidates scattered asserts (previously in test_tools.py / test_web.py)
into one table-driven suite. Additive only — no production code touched.

Isolation: suite-wide tmp DB/config via conftest.py; HOME-dependent checks
use monkeypatch so the real ~/.ssh is never touched.
"""

from sk.agent import _gated_dispatch
from sk.tools import (
    _url_blocked,
    dispatch_tool,
    tool_exec,
    tool_shell,
    tool_write_file,
)


CATASTROPHIC_SHELL = [
    "rm -rf /",
    "rm -rf /*",
    "sudo rm -rf ~",
    "sudo rm -rf $HOME",
    "mkfs.ext4 /dev/sda1",
    "mkfs /dev/sda",
    "dd if=x of=/dev/sda",
    "dd if=/dev/zero of=/dev/sda bs=1M",
    ":(){ :|:& };:",
    "echo hi > /dev/sda",
    "shred /dev/sda",
    "chmod -R 777 /",
]

WRITE_BLOCKED_PATHS = [
    "~/.ssh/evil",
    "~/.ssh/id_rsa",
    "~/.gnupg/secring",
    "/etc/evil",
    "/etc/passwd",
    "/usr/bin/evil",
    "/bin/evil",
    "/boot/evil",
    "/proc/evil",
    "/sys/evil",
    "/dev/evil",
]

SSRF_BLOCKED_URLS = [
    "http://localhost:11434/",
    "http://localhost/",
    "http://127.0.0.1/",
    "http://127.0.0.1:11434/",
    "http://169.254.169.254/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "ftp://example.com/x",
    "http://example.local/",
    "http://foo.internal/",
    "not a url",
]


def test_shell_hard_blocks_catastrophic():
    for bad in CATASTROPHIC_SHELL:
        out = tool_shell(bad)
        assert "blocked" in out.lower(), bad


def test_shell_allows_benign():
    assert "hello-shell" in tool_shell("echo hello-shell")
    assert "ok" in dispatch_tool("shell", {"cmd": "echo ok"})


def test_write_blocklist_sensitive_paths():
    for p in WRITE_BLOCKED_PATHS:
        out = tool_write_file(p, "x")
        assert "blocked" in out.lower() or "only allowed under" in out.lower(), p


def test_write_outside_home_tmp_refused(tmp_path, monkeypatch):
    import sk.tools as _t

    monkeypatch.setattr(_t.Path, "home", classmethod(lambda cls: tmp_path))
    # /etc is outside fake HOME and outside tmp -> must refuse
    assert "blocked" in _t.tool_write_file("/etc/sk-evil", "x").lower()
    assert "blocked" in _t.tool_make_dir("/etc/sk-evil").lower()
    assert "blocked" in _t.tool_delete_file("/etc/sk-evil").lower()


def test_ssrf_url_blocked_table():
    for url in SSRF_BLOCKED_URLS:
        assert _url_blocked(url) is not None, url


def test_ssrf_dispatch_blocks_private():
    for url in ("http://127.0.0.1:11434/", "http://localhost:11434/"):
        out = dispatch_tool("read_url", {"url": url})
        assert "blocked" in out.lower() or "error" in out.lower(), url


def test_exec_allowlist_blocks():
    # rm is not in ALLOWED_BINARIES at all
    assert "not in allowlist" in tool_exec("rm -rf /") or "Blocked" in tool_exec("rm -rf /")
    # chaining / redirection metachars blocked
    for bad in ("echo hi; rm -rf /", "ls | cat", "echo hi > /tmp/x", "echo $(whoami)"):
        assert "Blocked" in tool_exec(bad), bad
    # find -exec / -delete blocked
    assert "Blocked" in tool_exec("find /tmp -exec rm {} \\;")
    assert "Blocked" in tool_exec("find /tmp -delete")
    # git only read-only subcommands
    assert "Blocked" in tool_exec("git push")
    # benign still works
    assert "exit 0" in tool_exec("pwd")


def test_approval_gate_denies_writes_and_shell():
    for name, args in [
        ("shell", {"cmd": "echo hi"}),
        ("delete_file", {"path": "/tmp/x"}),
        ("make_dir", {"path": "/tmp/x"}),
        ("write_file", {"path": "/tmp/x", "content": "hi"}),
    ]:
        out, ok = _gated_dispatch(name, args, approve=lambda n, a: False)
        assert ok is False and "Denied" in out, name
