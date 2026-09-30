"""Security regression lock: destructive shell, write blocklist, SSRF, exec allowlist.

Consolidates scattered asserts (previously in test_tools.py / test_web.py)
into one table-driven suite. Additive only — no production code touched.

Isolation: suite-wide tmp DB/config via conftest.py; HOME-dependent checks
use monkeypatch so the real ~/.ssh is never touched.
"""

from sk.agent import _gated_dispatch
from sk.tools import (
    _check_cmd,
    _url_blocked,
    dispatch_tool,
    tool_exec,
    tool_list_dir,
    tool_read_file,
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
    # #264: quoted/braced spellings normalize to the bare forms above
    'rm -rf "$HOME"',
    "rm -rf '$HOME'",
    "rm -rf ${HOME}",
    "rm -rf ${HOME}/",
    'rm -rf "~"',
    "rm -rf '/etc'",
    'sudo rm -rf "$HOME"',
    'dd if=x of="/dev/sda"',
    # #264: bare system roots (bare, trailing slash, /* glob)
    "rm -rf /etc",
    "rm -rf /etc/",
    "rm -rf /etc/*",
    "rm -rf /proc",
    "rm -rf /sys",
    "rm -rf /dev",
    "rm -rf /usr",
    "rm -rf /boot",
    "rm -rf /boot/*",
    "rm --recursive /usr",
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


def test_shell_block_allows_targeted_paths():
    """#264: targeted subpaths stay approval-gated (only bare roots hard-block)."""
    from sk.tools.shell import _check_shell

    for ok in (
        "rm -rf /tmp/x",
        "rm -rf ~/proj",
        "rm -rf $HOME/tmp",
        "rm -rf ${HOME}/tmp",
        "rm -rf /etc/hostname",
        "rm /etc/hostname",
        "rm -rf /etc2",
        "ls /etc",
    ):
        assert _check_shell(ok) is None, ok


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


def test_session_allowlist_gates_shell_prefix():
    """#40: allowlisted shell prefix executes, sibling prompts (denied here)."""
    from sk.config import is_session_allowed

    allow = ("shell:pytest",)
    approve = lambda n, a: is_session_allowed(n, a, allow)  # noqa: E731
    out, ok = _gated_dispatch("shell", {"cmd": "pytest -q"}, approve=approve, session="t")
    assert ok is True and "Denied" not in out
    out, ok = _gated_dispatch("shell", {"cmd": "pytest-x"}, approve=approve, session="t")
    assert ok is False and "Denied" in out
    # hard-refusals win even when approved: bare approval can't save rm -rf /
    out, ok = _gated_dispatch("shell", {"cmd": "rm -rf /"}, approve=lambda n, a: True, session="t")
    assert "refused even with approval" in out


def test_plugin_shell_template_cannot_escape_allowlist(tmp_path, monkeypatch):
    """#49: plugin shell tools run the exec allowlist pipeline, not raw shell."""
    import sk.plugins as plugins
    import sk.skills as skills

    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path / "skills")
    plugins.clear_plugin_cache()
    try:
        d = skills.SKILLS_DIR / "evilpack"
        d.mkdir(parents=True, exist_ok=True)
        (d / "TOOLS.md").write_text(
            "---\ntool: evil\nkind: shell-template\ncmd: rm {target}\n"
            "params: target: string required\n---\n"
        )
        from sk.tools import dispatch_tool

        out = dispatch_tool("evil", {"target": "/tmp/x"})
        assert "Blocked" in out or "allowlist" in out
        # and the ask-gate still prompts even for evil packs
        out, ok = _gated_dispatch(
            "evil", {"target": "/tmp/x"}, approve=lambda n, a: False, session="t"
        )
        assert ok is False and "Denied" in out
    finally:
        plugins.clear_plugin_cache()


def test_plan_mode_denies_writes_without_prompting(tmp_path, monkeypatch):
    """#207: plan mode is a dispatch-level guarantee, not prompt politeness."""
    import sk.store as store
    from sk.tools import PLAN_DENIED_TOOLS

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    assert {"write_file", "edit_file", "make_dir", "delete_file"} <= PLAN_DENIED_TOOLS
    assert "shell" not in PLAN_DENIED_TOOLS  # shell exploration stays gated, not banned

    def _boom(name, args):
        raise AssertionError("doomed calls must never prompt")

    for tool, args in [
        ("write_file", {"path": "/tmp/x", "content": "hi"}),
        ("edit_file", {"path": "/tmp/x", "old_string": "a", "new_string": "b"}),
        ("make_dir", {"path": "/tmp/x"}),
    ]:
        out, ok = _gated_dispatch(tool, args, approve=_boom, session="t", plan_mode=True)
        assert ok is False and "plan mode" in out, tool
    rows = store.list_tool_runs("t")
    assert any(r["tool"] == "write_file" and not r["approved"] for r in rows)


def test_plan_mode_still_allows_reads_and_shell(tmp_path, monkeypatch):
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    out, ok = _gated_dispatch(
        "list_dir", {"path": "/tmp"}, approve=lambda n, a: True, session="t", plan_mode=True
    )
    assert ok is True
    out, ok = _gated_dispatch(
        "exec", {"cmd": "pwd"}, approve=lambda n, a: True, session="t", plan_mode=True
    )
    assert ok is False and "plan mode" in out  # #263: exec denied in plan mode
    out, ok = _gated_dispatch(
        "shell", {"cmd": "echo hi"}, approve=lambda n, a: True, session="t", plan_mode=True
    )
    assert ok is True and "hi" in out


def test_readonly_denies_all_gated_tools(tmp_path, monkeypatch):
    import sk.store as store
    from sk.tools import READONLY_DENIED_TOOLS, APPROVAL_TOOLS

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    assert READONLY_DENIED_TOOLS == set(APPROVAL_TOOLS) | {"exec"}  # #263: exec denied too

    def _boom(name, args):
        raise AssertionError("doomed calls must never prompt")

    for tool, args in [
        ("write_file", {"path": "/tmp/x", "content": "hi"}),
        ("shell", {"cmd": "echo hi"}),
    ]:
        out, ok = _gated_dispatch(tool, args, approve=_boom, session="t", read_only=True)
        assert ok is False and "read-only" in out, tool
    out, ok = _gated_dispatch(
        "exec", {"cmd": "pwd"}, approve=lambda n, a: True, session="t", read_only=True
    )
    assert ok is False and "read-only" in out  # #263: exec denied in readonly
    out, ok = _gated_dispatch(
        "list_dir", {"path": "/tmp"}, approve=lambda n, a: True, session="t", read_only=True
    )
    assert ok is True  # true reads still flow


def test_normal_mode_unaffected_by_gates(tmp_path, monkeypatch):
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    f = tmp_path / "ok.txt"
    out, ok = _gated_dispatch(
        "write_file",
        {"path": str(f), "content": "hi"},
        approve=lambda n, a: True,
        session="t",
    )
    assert ok is True and f.read_text() == "hi"


def test_deny_list_beats_allow_at_approver():
    """#234: contradictory --allow + --deny resolves to deny, no prompt."""
    from sk.cli.approvers import _make_approver

    calls: list = []

    def _boom(name, args):
        calls.append((name, args))
        raise AssertionError("deny must short-circuit before any prompt")

    import sk.cli.approvers as approvers

    real_confirm = approvers.typer.confirm
    approvers.typer.confirm = _boom
    try:
        approve = _make_approver(False, (), ("shell",), deny=("shell:rm",))
        assert approve("shell", {"cmd": "rm -rf /"}) is False
        assert approve("shell", {"cmd": "ls"}) is True
    finally:
        approvers.typer.confirm = real_confirm
    assert calls == []


def test_exec_python3_blocked_no_code_execution(tmp_path):
    """#263 PoC: file-based payload without metacharacters must not run."""
    evil = tmp_path / "evil.py"
    evil.write_text(
        "import pathlib; pathlib.Path(__file__).with_name('exec_proof.txt').write_text('pwned')\n"
    )
    out = tool_exec(f"python3 {evil}")
    assert "Blocked" in out and "not in allowlist" in out
    assert not (tmp_path / "exec_proof.txt").exists()  # never executed
    assert "Blocked" in tool_exec('python3 -c "print(1)"')
    assert "Blocked" in tool_exec("python3 -m http.server")


def test_exec_ollama_restricted_to_inventory():
    """#263: ollama run/pull/push/serve escape read-only; only list/show/ps pass."""
    assert isinstance(_check_cmd("ollama list"), tuple)
    assert isinstance(_check_cmd("ollama show llama3.2:3b"), tuple)
    for bad in (
        "ollama",
        "ollama run llama3.2:3b hi",
        "ollama pull llama3.2:3b",
        "ollama push my/model",
        "ollama serve",
        "ollama cp a b",
    ):
        out = _check_cmd(bad)
        assert isinstance(out, str) and "Blocked" in out, bad


def test_exec_denied_in_readonly_and_plan():
    """#263: exec bypassed both hard modes; now it is denied in each."""
    out, ok = _gated_dispatch("exec", {"cmd": "pwd"}, read_only=True)
    assert ok is False and "read-only mode" in out
    out, ok = _gated_dispatch("exec", {"cmd": "pwd"}, plan_mode=True)
    assert ok is False and "plan mode" in out


def test_exec_still_approval_free_normally():
    """Gating is mode-only: everyday inventory commands never prompt."""
    out, ok = _gated_dispatch("exec", {"cmd": "pwd"}, approve=lambda n, a: True)
    assert ok is True and "exit 0" in out


def _fake_home_with_secrets(tmp_path, monkeypatch):
    """Fake HOME holding a key + history DB. Never touches the real ~/.ssh."""
    import sk.tools as _t

    monkeypatch.setattr(_t.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))  # for expanduser("~")
    (tmp_path / ".ssh").mkdir(exist_ok=True)
    (tmp_path / ".ssh" / "id_rsa").write_text("SECRET-KEY-MATERIAL")
    (tmp_path / ".sidekick").mkdir(exist_ok=True)
    (tmp_path / ".sidekick" / "history.db").write_text("secret-history")
    return tmp_path


def test_read_sensitive_paths_blocked(tmp_path, monkeypatch):
    """#265 PoCs: keys + history DB unreadable via read_file/list_dir/exec."""
    home = _fake_home_with_secrets(tmp_path, monkeypatch)
    key = str(home / ".ssh" / "id_rsa")
    out = tool_read_file(key)
    assert "blocked" in out.lower() and "SECRET-KEY-MATERIAL" not in out
    out = tool_list_dir(str(home / ".ssh"))
    assert "blocked" in out.lower() and "id_rsa" not in out
    out = tool_read_file(str(home / ".sidekick" / "history.db"))
    assert "blocked" in out.lower() and "secret-history" not in out
    assert "blocked" in tool_read_file("~/.ssh/id_rsa").lower()  # ~ expansion
    assert "blocked" in tool_exec(f"cat {key}").lower()
    assert "blocked" in tool_exec(f"head -c 10 {key}").lower()
    assert "blocked" in tool_exec(f"ls {home / '.ssh'}").lower()


def test_read_nonsensitive_stays_open(tmp_path, monkeypatch):
    """#265 is scoped: /etc grounding + tmp files + plain inventory still flow."""
    _fake_home_with_secrets(tmp_path, monkeypatch)
    f = tmp_path / "notes.txt"
    f.write_text("hello")
    assert "hello" in tool_read_file(str(f))
    assert "notes.txt" in tool_list_dir(str(tmp_path))
    assert "blocked" not in tool_exec("cat /etc/hostname").lower()
    assert "blocked" not in tool_exec("ls /tmp").lower()
    assert "blocked" not in tool_exec(f"cat {f}").lower()


def test_read_sensitive_cwd(tmp_path, monkeypatch):
    """#265: bare ls/du/list_dir inside ~/.ssh still refuse; pwd/echo unaffected."""
    home = _fake_home_with_secrets(tmp_path, monkeypatch)
    monkeypatch.chdir(home / ".ssh")
    assert "blocked" in tool_exec("ls").lower()
    assert "blocked" in tool_list_dir(".").lower()
    assert "blocked" not in tool_exec("pwd").lower()
    assert "blocked" not in tool_exec("echo hi").lower()
    assert "blocked" not in tool_list_dir("/tmp").lower()
