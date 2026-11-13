"""OS-level sandbox enforcement (Phase 2 of #440).

The declarative policy is enforced by reading the command text. This is the
other half: the boundary the kernel enforces, when the machine has the tooling
for it — and never at the cost of breaking an install that does not.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from sk import sandbox, sandbox_os


@pytest.fixture(autouse=True)
def _no_policy_leak():
    """The policy cache is process-wide; a policy left behind would enforce
    itself in the next test."""
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
    (project / "a.txt").write_text("hello\n")
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


# --- detection -------------------------------------------------------------


def test_detection_never_raises():
    enforcement = sandbox_os.detect()
    assert enforcement.backend in ("", "bwrap", "seatbelt")
    assert enforcement.reason, "a detection without a reason says nothing"


def test_a_backend_that_does_not_work_is_not_enforcement(monkeypatch):
    """A broken profile must not break the shell — and must not be reported as
    enforcement either."""
    monkeypatch.setattr(sandbox_os, "probe", lambda backend: (False, "exit 1"))
    sandbox_os._probe_cache.clear()
    try:
        enforcement = sandbox_os.detect()
        assert enforcement.available is False
        assert "does not work" in enforcement.reason or "exit 1" in enforcement.reason
    finally:
        sandbox_os._probe_cache.clear()


def test_the_probe_runs_the_backend_before_trusting_it():
    """The enforcement is only claimed after the sandbox has run something."""
    if not shutil.which("bwrap"):
        pytest.skip("bubblewrap is not installed")
    ok, reason = sandbox_os.probe("bwrap")
    assert ok is True, reason


def test_detection_finds_bubblewrap_when_it_is_installed():
    if not shutil.which("bwrap"):
        pytest.skip("bubblewrap is not installed")
    enforcement = sandbox_os.detect()
    assert enforcement.available is True
    assert enforcement.backend == "bwrap"


def test_a_machine_without_the_tooling_is_advisory_not_broken():
    """Never fail closed on a missing optional dependency."""
    enforcement = sandbox_os.Enforcement(available=False, reason="not installed")
    assert "advisory" in enforcement.describe()


# --- command construction --------------------------------------------------


def _resolved(path: str) -> str:
    """The path as the sandbox sees it: symlinks resolved.

    macOS resolves /tmp to /private/tmp, and the sandbox binds the resolved
    path — the tests have to speak the same language as the implementation.
    """
    return str(Path(path).resolve())


def test_bubblewrap_binds_the_filesystem_read_only():
    cmd = sandbox_os.bwrap_command("echo hi", write_roots=["/tmp/x"], cwd="/tmp")
    assert cmd.startswith("bwrap ")
    assert "--ro-bind / /" in cmd
    assert f"--bind {_resolved('/tmp/x')} {_resolved('/tmp/x')}" in cmd


def test_the_tmpfs_comes_before_the_binds():
    """Otherwise a root under /tmp is shadowed by the fresh tmpfs and the
    process cannot see its own working directory."""
    root = _resolved("/tmp/x")
    cmd = sandbox_os.bwrap_command("echo hi", write_roots=["/tmp/x"], cwd="/tmp/x")
    assert cmd.index("--tmpfs /tmp") < cmd.index(f"--bind {root} {root}")


def test_bubblewrap_denies_the_network_when_no_host_is_allowed():
    cmd = sandbox_os.bwrap_command("echo hi", write_roots=[], cwd="/tmp", allow_network=False)
    assert "--unshare-net" in cmd
    cmd = sandbox_os.bwrap_command("echo hi", write_roots=[], cwd="/tmp", allow_network=True)
    assert "--unshare-net" not in cmd


def test_the_working_directory_is_always_bound():
    cmd = sandbox_os.bwrap_command("echo hi", write_roots=[], cwd="/home/me/project")
    root = _resolved("/home/me/project")
    assert f"--bind {root} {root}" in cmd


def test_the_seatbelt_profile_denies_by_default():
    profile = sandbox_os.seatbelt_profile(write_roots=["/tmp/x"], cwd="/tmp")
    assert profile.splitlines()[1] == "(deny default)"
    assert f'(allow file-write* (subpath "{_resolved("/tmp/x")}"))' in profile
    assert "(allow network*)" not in profile


def test_the_seatbelt_profile_allows_the_network_when_asked():
    profile = sandbox_os.seatbelt_profile(write_roots=[], cwd="/tmp", allow_network=True)
    assert "(allow network*)" in profile


def test_wrapping_without_the_tooling_returns_the_command_unchanged(monkeypatch):
    monkeypatch.setattr(
        sandbox_os,
        "detect",
        lambda: sandbox_os.Enforcement(available=False, reason="not installed"),
    )
    cmd, note = sandbox_os.wrap_command("echo hi")
    assert cmd == "echo hi"
    assert note == ""


# --- enforcement, for real -------------------------------------------------


@pytest.mark.skipif(not shutil.which("bwrap"), reason="needs bubblewrap")
def test_a_write_outside_the_roots_fails(tmp_path):
    project = _project(tmp_path)
    outside = tmp_path / "outside.txt"
    cmd = sandbox_os.bwrap_command(
        f"echo leaked > {outside}",
        write_roots=[str(project)],
        cwd=str(project),
    )
    result = subprocess.run(
        ["bash", "-c", cmd], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode != 0 or not outside.exists(), "the write outside the roots succeeded"
    assert not outside.exists()


@pytest.mark.skipif(not shutil.which("bwrap"), reason="needs bubblewrap")
def test_a_write_inside_the_roots_succeeds(tmp_path):
    project = _project(tmp_path)
    inside = project / "inside.txt"
    cmd = sandbox_os.bwrap_command(
        f"echo ok > {inside}", write_roots=[str(project)], cwd=str(project)
    )
    result = subprocess.run(
        ["bash", "-c", cmd], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    assert inside.read_text().strip() == "ok"


@pytest.mark.skipif(not shutil.which("bwrap"), reason="needs bubblewrap")
def test_the_network_is_gone_when_the_policy_allows_no_host(tmp_path):
    project = _project(tmp_path)
    cmd = sandbox_os.bwrap_command(
        "curl -s -m 5 http://example.com > /dev/null && echo REACHED || echo BLOCKED",
        write_roots=[str(project)],
        cwd=str(project),
        allow_network=False,
    )
    result = subprocess.run(
        ["bash", "-c", cmd], capture_output=True, text=True, timeout=60, check=False
    )
    assert "BLOCKED" in result.stdout, result.stdout


# --- the tool layer --------------------------------------------------------


def test_the_shell_tool_wraps_the_command_when_the_policy_is_enforced(tmp_path, monkeypatch):
    import sk.config as config_mod
    import sk.tools.shell as shell_mod

    project = _project(tmp_path)
    _policy(tmp_path, ENFORCED)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.chdir(project)
    sandbox.reset_cache()

    wrapped, backend = shell_mod._sandbox_wrap("echo hi", str(project))
    if not sandbox_os.detect().available:
        assert wrapped == "echo hi"
        return
    assert backend in ("bwrap", "seatbelt")
    # The command names the binary, which is not the backend's name.
    assert ("bwrap" if backend == "bwrap" else "sandbox-exec") in wrapped


def test_an_advisory_policy_does_not_wrap(tmp_path, monkeypatch):
    import sk.config as config_mod
    import sk.tools.shell as shell_mod

    project = _project(tmp_path)
    _policy(tmp_path, 'schema = 1\nmode = "advisory"\n')
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.chdir(project)
    sandbox.reset_cache()

    wrapped, backend = shell_mod._sandbox_wrap("echo hi", str(project))
    assert wrapped == "echo hi"
    assert backend == ""


def test_no_policy_means_no_wrapping(tmp_path, monkeypatch):
    import sk.config as config_mod
    import sk.tools.shell as shell_mod

    project = _project(tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.chdir(project)
    sandbox.reset_cache()

    wrapped, backend = shell_mod._sandbox_wrap("echo hi", str(project))
    assert wrapped == "echo hi"
    assert backend == ""


# --- visibility ------------------------------------------------------------


def test_the_enforcement_mode_is_reported(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    sandbox.reset_cache()
    assert sandbox.enforcement_mode() == "off"

    _policy(tmp_path, ENFORCED)
    sandbox.reset_cache()
    mode = sandbox.enforcement_mode()
    assert mode in ("enforced", "advisory")
    if sandbox_os.detect().available:
        assert mode == "enforced"
    else:
        assert mode == "advisory"


def test_the_status_line_names_the_os_enforcement(tmp_path, monkeypatch):
    import sk.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    _policy(tmp_path, ENFORCED)
    sandbox.reset_cache()
    line = sandbox.status_line()
    assert "os enforcement:" in line


def test_doctor_and_config_show_the_enforcement(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    import sk.config as config_mod
    from sk.cli import app

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    _policy(tmp_path, ENFORCED)
    sandbox.reset_cache()

    result = CliRunner().invoke(app, ["config", "--show"])
    assert result.exit_code == 0, result.output
    assert "sandbox=" in result.output

    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "sandbox=" in result.output
