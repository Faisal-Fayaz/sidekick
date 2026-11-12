"""The declarative sandbox (#329): policy, enforcement, and the bypass corpus.

The shell blocklist is a denylist over strings, and a denylist over shell
strings is structurally incomplete (#288). The sandbox is the other plane:
permission decides *whether* a call is allowed, the sandbox decides *what* it
can reach. These tests pin both halves — the policy that is loaded, and the
calls that must be refused once it is.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sk import sandbox


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Every test gets its own config dir and a fresh policy cache."""
    import sk.config as config_mod

    config_dir = tmp_path / ".sidekick"
    config_dir.mkdir()
    monkeypatch.setattr(config_mod, "CONFIG_DIR", config_dir)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    sandbox.reset_cache()
    yield
    sandbox.reset_cache()


def _policy(root: Path, body: str) -> Path:
    path = root / ".sidekick" / "sandbox.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    (project / "app.py").write_text("x = 1\n")
    return project


ENFORCED = """
schema = 1
mode = "enforced"

[filesystem]
read_roots = []
write_roots = []

[network]
allow = []
"""


# --- loading ---------------------------------------------------------------


def test_no_policy_file_means_no_sandbox(tmp_path):
    policy = sandbox.load_policy(project_root=str(tmp_path))
    assert policy.mode == "off"
    assert policy.path == ""
    assert policy.active is False
    assert "no policy file" in policy.describe()


def test_policy_is_read_from_the_user_config_dir(tmp_path):
    _policy(tmp_path, ENFORCED)
    policy = sandbox.load_policy(project_root=str(tmp_path))
    assert policy.mode == "enforced"
    assert policy.errors == ()
    assert policy.path.endswith("sandbox.toml")


def test_broken_policy_is_reported_not_ignored(tmp_path):
    _policy(tmp_path, "this is not toml = = =")
    policy = sandbox.load_policy(project_root=str(tmp_path))
    assert policy.errors, "a broken policy must say so"
    assert policy.mode == "off"


def test_unknown_schema_is_an_error(tmp_path):
    _policy(tmp_path, 'schema = 99\nmode = "enforced"\n')
    policy = sandbox.load_policy(project_root=str(tmp_path))
    assert any("schema" in e for e in policy.errors)


def test_unknown_mode_is_an_error(tmp_path):
    _policy(tmp_path, 'schema = 1\nmode = "yolo"\n')
    policy = sandbox.load_policy(project_root=str(tmp_path))
    assert any("mode" in e for e in policy.errors)
    assert policy.mode == "off"


def test_policy_without_roots_warns(tmp_path):
    _policy(tmp_path, ENFORCED)
    policy = sandbox.load_policy(project_root=str(tmp_path))
    assert any("no extra roots" in w for w in policy.warnings)


# --- path enforcement ------------------------------------------------------


def test_read_inside_the_project_is_allowed(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _policy(tmp_path, ENFORCED)
    monkeypatch.chdir(project)
    assert sandbox.check_tool("read_file", {"path": str(project / "app.py")}) is None


def test_read_outside_the_project_is_denied(tmp_path):
    _project(tmp_path)
    _policy(tmp_path, ENFORCED)
    denial = sandbox.check_tool("read_file", {"path": "/etc/passwd"})
    assert denial is not None
    assert "read denied" in denial


def test_write_outside_the_project_is_denied(tmp_path):
    _project(tmp_path)
    _policy(tmp_path, ENFORCED)
    denial = sandbox.check_tool("write_file", {"path": "/tmp/evil.py", "content": "x"})
    assert denial is not None
    assert "write denied" in denial


def test_write_inside_the_project_is_allowed(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _policy(tmp_path, ENFORCED)
    monkeypatch.chdir(project)
    assert sandbox.check_tool("write_file", {"path": str(project / "new.py")}) is None


def test_declared_read_root_is_allowed(tmp_path):
    _project(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.md").write_text("hi")
    _policy(
        tmp_path,
        f"""
schema = 1
mode = "enforced"
[filesystem]
read_roots = ["{other}"]
""",
    )
    assert sandbox.check_tool("read_file", {"path": str(other / "notes.md")}) is None


def test_declared_read_root_does_not_grant_writes(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _policy(
        tmp_path,
        f"""
schema = 1
mode = "enforced"
[filesystem]
read_roots = ["{other}"]
""",
    )
    denial = sandbox.check_tool("write_file", {"path": str(other / "x.py")})
    assert denial is not None and "write denied" in denial


def test_deny_list_beats_an_allowed_root(tmp_path):
    secret = tmp_path / "secrets"
    secret.mkdir()
    _policy(
        tmp_path,
        f"""
schema = 1
mode = "enforced"
[filesystem]
read_roots = ["{tmp_path}"]
deny = ["{secret}"]
""",
    )
    denial = sandbox.check_tool("read_file", {"path": str(secret / "id_rsa")})
    assert denial is not None and "deny list" in denial


def test_a_symlink_out_of_the_project_is_denied(tmp_path):
    project = _project(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = project / "link.txt"
    link.symlink_to(outside)
    _policy(tmp_path, ENFORCED)
    denial = sandbox.check_tool("read_file", {"path": str(link)})
    assert denial is not None and "read denied" in denial


def test_relative_escape_is_denied(tmp_path):
    _project(tmp_path)
    _policy(tmp_path, ENFORCED)
    denial = sandbox.check_tool("read_file", {"path": "../../etc/passwd"})
    assert denial is not None and "read denied" in denial


# --- the bypass corpus -----------------------------------------------------
#
# #288 showed the shell blocklist can be walked around with ordinary shell
# indirection. The sandbox does not rely on the blocklist: it reads the paths
# and hosts a command mentions, wherever they appear. Every command here
# reaches outside the project root, and every one must be refused.


BYPASS_COMMANDS = [
    "cat /etc/passwd",
    "R=cat; $R /etc/passwd",
    "bash -c 'cat /etc/passwd'",
    'sh -c "cat /etc/shadow"',
    "cat ${HOME}/.ssh/id_rsa",
    "cat $HOME/.aws/credentials",
    "cd /etc && cat passwd",
    "cat < /etc/passwd",
    "head -1 /etc/shadow",
    "grep root /etc/passwd",
    "less /etc/passwd",
    "tail /etc/passwd",
    "python3 -c \"print(open('/etc/passwd').read())\"",
    "cat /etc/passwd | base64",
    "tar -cf - /etc | tar -xf -",
]


@pytest.mark.parametrize("command", BYPASS_COMMANDS)
def test_bypass_corpus_cannot_read_outside_the_roots(tmp_path, command):
    project = _project(tmp_path)
    _policy(tmp_path, ENFORCED)
    monkey = os.getcwd()
    try:
        os.chdir(project)
        denial = sandbox.check_tool("shell", {"cmd": command})
    finally:
        os.chdir(monkey)
    assert denial is not None, f"the sandbox allowed: {command}"
    assert "sandbox:" in denial


BYPASS_WRITES = [
    "echo pwned > /etc/hosts",
    "cp /etc/passwd /tmp/x",
    "rm /etc/hosts",
    "mv /etc/hosts /tmp/hosts",
    "tee /etc/hosts < /etc/passwd",
    "touch /etc/sidekick-test",
    "install -m 644 /etc/passwd /tmp/x",
    "cat /etc/passwd > /tmp/leak",
]


@pytest.mark.parametrize("command", BYPASS_WRITES)
def test_bypass_corpus_cannot_write_outside_the_roots(tmp_path, command):
    project = _project(tmp_path)
    _policy(tmp_path, ENFORCED)
    monkey = os.getcwd()
    try:
        os.chdir(project)
        denial = sandbox.check_tool("shell", {"cmd": command})
    finally:
        os.chdir(monkey)
    assert denial is not None, f"the sandbox allowed: {command}"
    assert "sandbox:" in denial


NETWORK_COMMANDS = [
    "curl http://example.com/x",
    "curl -s https://example.com/x",
    "wget https://example.com/x",
    "ssh user@example.com",
    "nc example.com 443",
    "python3 -c \"import urllib.request; urllib.request.urlopen('http://example.com')\"",
    "curl http://169.254.169.254/latest/meta-data/",
]


@pytest.mark.parametrize("command", NETWORK_COMMANDS)
def test_bypass_corpus_cannot_reach_the_network(tmp_path, command):
    project = _project(tmp_path)
    _policy(tmp_path, ENFORCED)
    monkey = os.getcwd()
    try:
        os.chdir(project)
        denial = sandbox.check_tool("shell", {"cmd": command})
    finally:
        os.chdir(monkey)
    assert denial is not None, f"the sandbox allowed: {command}"
    assert "network denied" in denial


def test_allowed_host_is_reachable(tmp_path):
    project = _project(tmp_path)
    _policy(
        tmp_path,
        """
schema = 1
mode = "enforced"
[network]
allow = ["api.openai.com"]
""",
    )
    monkey = os.getcwd()
    try:
        os.chdir(project)
        denial = sandbox.check_tool("shell", {"cmd": "curl https://api.openai.com/v1/models"})
    finally:
        os.chdir(monkey)
    assert denial is None


def test_ordinary_commands_still_run(tmp_path):
    project = _project(tmp_path)
    _policy(tmp_path, ENFORCED)
    monkey = os.getcwd()
    try:
        os.chdir(project)
        for command in ("ls -la", "git status", "pytest tests/ -q", "cat app.py"):
            assert sandbox.check_tool("shell", {"cmd": command}) is None, command
    finally:
        os.chdir(monkey)


# --- modes -----------------------------------------------------------------


def test_advisory_mode_reports_instead_of_blocking(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _policy(
        tmp_path,
        """
schema = 1
mode = "advisory"
""",
    )
    monkeypatch.chdir(project)
    policy, roots = sandbox.get_policy()
    assert policy.mode == "advisory"
    assert policy.enforcing is False
    # The violation is still reported...
    violation = sandbox.check_tool("read_file", {"path": "/etc/passwd"})
    assert violation is not None and "read denied" in violation
    # ...but it does not block the call.
    from sk.tools.registry import dispatch_tool

    result = dispatch_tool("read_file", {"path": str(project / "app.py")})
    assert "x = 1" in result
    note = sandbox.note_for(policy, violation)
    assert "sandbox advisory" in note


def test_off_mode_never_blocks(tmp_path):
    _policy(tmp_path, ENFORCED)
    policy = sandbox.load_policy(project_root=str(tmp_path))
    assert policy.mode == "enforced"


def test_a_broken_policy_refuses_rather_than_widening(tmp_path):
    _policy(tmp_path, "schema = 1\nmode = 'enforced'\nfilesystem = 3\n")
    policy = sandbox.load_policy(project_root=str(tmp_path))
    if policy.errors:
        denial = sandbox.check_tool("read_file", {"path": "/etc/passwd"})
        assert denial is not None and "could not be read" in denial


# --- the tool layer --------------------------------------------------------


def test_dispatch_refuses_a_denied_read(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _policy(tmp_path, ENFORCED)
    monkeypatch.chdir(project)
    from sk.tools.registry import dispatch_tool

    result = dispatch_tool("read_file", {"path": "/etc/passwd"})
    assert "sandbox:" in result
    assert "root:" not in result  # the file was never opened


def test_dispatch_still_allows_an_inside_read(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _policy(tmp_path, ENFORCED)
    monkeypatch.chdir(project)
    from sk.tools.registry import dispatch_tool

    result = dispatch_tool("read_file", {"path": str(project / "app.py")})
    assert "x = 1" in result


def test_dispatch_refuses_a_denied_shell_command(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _policy(tmp_path, ENFORCED)
    monkeypatch.chdir(project)
    from sk.tools.registry import dispatch_tool

    result = dispatch_tool("shell", {"cmd": "cat /etc/passwd"})
    assert "sandbox:" in result


def test_the_sandbox_refuses_before_the_approval_prompt(tmp_path, monkeypatch):
    """A call the sandbox will refuse must not ask the user to approve it."""
    project = _project(tmp_path)
    _policy(tmp_path, ENFORCED)
    monkeypatch.chdir(project)
    import sk.agent as agent

    asked = []

    def approve(name, args):
        asked.append(name)
        return True

    result, approved = agent._gated_dispatch(
        "shell", {"cmd": "cat /etc/passwd"}, approve=approve, session="sandbox"
    )
    assert approved is False
    assert "sandbox:" in result
    assert asked == [], "the user was asked to approve a call that cannot run"


def test_advisory_mode_appends_a_note_to_the_result(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _policy(tmp_path, 'schema = 1\nmode = "advisory"\n')
    monkeypatch.chdir(project)
    from sk.tools.registry import dispatch_tool

    result = dispatch_tool("read_file", {"path": "/etc/hostname"})
    assert "sandbox advisory" in result


def test_no_policy_file_means_the_tools_behave_as_before(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    from sk.tools.registry import dispatch_tool

    result = dispatch_tool("read_file", {"path": str(project / "app.py")})
    assert "x = 1" in result


# --- visibility ------------------------------------------------------------


def test_doctor_and_config_show_the_mode(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app
    import sk.config as config_mod

    _policy(tmp_path, ENFORCED)
    runner = CliRunner()
    result = runner.invoke(app, ["config", "--show"])
    assert result.exit_code == 0, result.output
    assert "sandbox=" in result.output
    assert "enforced" in result.output


def test_doctor_reports_the_sandbox(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sk.cli import app

    _policy(tmp_path, ENFORCED)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "sandbox=" in result.output
