"""Shell expansion in custom commands (#370).

`_run_shell` executes `sh -c` on a string from a user command template, and
coverage listed line 116 — the `subprocess.run(["sh", "-c", cmd])` call itself —
as never executed. Only the `if not cmd` guard had ever run, so the timeout,
non-zero-exit, truncation and error-containment paths were untested.

That matters more than ordinary coverage: a custom command's `!`cmd`` is expanded
before the model sees the prompt, so this is a subprocess boundary fed by a file
in the working tree.
"""

from __future__ import annotations

import subprocess

import pytest

import sk.custom_commands as cc


def test_empty_command_runs_nothing(monkeypatch):
    def _never(*a, **kw):
        raise AssertionError("must not spawn a shell for an empty command")

    monkeypatch.setattr(cc.subprocess, "run", _never)
    for value in ("", "   ", "\n\t "):
        assert cc._run_shell(value) == ""


def test_runs_through_sh_dash_c_and_returns_stdout(monkeypatch):
    """The shell is `sh -c`, not a split argv: these commands use pipes,
    globs and `$(...)`, which argv-splitting would break."""
    seen = {}

    def _fake(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "expanded text\n", "")

    monkeypatch.setattr(cc.subprocess, "run", _fake)
    assert cc._run_shell("echo hi | tr a-z A-Z") == "expanded text\n"
    assert seen["argv"][:2] == ["sh", "-c"]
    assert seen["argv"][2] == "echo hi | tr a-z A-Z"


def test_pipelines_and_command_substitution_actually_work(monkeypatch, tmp_path):
    """Real `sh`, not a mock: prove the command shape we document is the shape
    that runs. Skipped on platforms without a POSIX sh."""
    import shutil

    if not shutil.which("sh"):
        pytest.skip("no sh on this platform")
    assert cc._run_shell("printf 'a b c' | tr ' ' '-'").strip() == "a-b-c"
    assert cc._run_shell("echo nested-$(echo inner)").strip() == "nested-inner"


def test_non_zero_exit_becomes_a_note_with_stderr(monkeypatch):
    monkeypatch.setattr(
        cc.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 3, "", "bad flag\n"),
    )
    out = cc._run_shell("false")
    assert "shell expansion failed" in out
    assert "exit 3" in out
    assert "bad flag" in out


def test_non_zero_exit_without_stderr_is_still_explained(monkeypatch):
    monkeypatch.setattr(
        cc.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "")
    )
    out = cc._run_shell("false")
    assert "shell expansion failed" in out
    assert "exit 1" in out


def test_timeout_becomes_a_note_and_never_escapes(monkeypatch):
    """A hung command must not hang the turn."""

    def _timeout(argv, **kw):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout"))

    monkeypatch.setattr(cc.subprocess, "run", _timeout)
    out = cc._run_shell("sleep 300")
    assert "timed out" in out
    assert f"{cc.SHELL_TIMEOUT:g}s" in out


def test_spawn_failure_becomes_a_note_and_never_escapes(monkeypatch):
    monkeypatch.setattr(
        cc.subprocess, "run", lambda argv, **kw: (_ for _ in ()).throw(OSError("no sh"))
    )
    out = cc._run_shell("echo hi")
    assert "shell expansion failed" in out
    assert "no sh" in out


def test_timeout_is_always_applied(monkeypatch):
    """Without a timeout this is an unbounded wait driven by a file in cwd."""
    seen = {}

    def _fake(argv, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(cc.subprocess, "run", _fake)
    cc._run_shell("echo hi")
    assert seen["timeout"] == cc.SHELL_TIMEOUT


def test_oversized_output_is_truncated_with_a_marker(monkeypatch):
    monkeypatch.setattr(
        cc.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, "x" * (cc.MAX_EXPANSION_BYTES + 500), ""
        ),
    )
    out = cc._run_shell("yes")
    assert "truncated" in out
    assert len(out) < cc.MAX_EXPANSION_BYTES + 500


def test_output_exactly_at_the_limit_is_not_marked_truncated(monkeypatch):
    monkeypatch.setattr(
        cc.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "y" * cc.MAX_EXPANSION_BYTES, ""),
    )
    assert "truncated" not in cc._run_shell("yes")


def test_stderr_of_a_successful_command_is_dropped(monkeypatch):
    """A command that succeeds while writing to stderr must not have that noise
    spliced into the prompt handed to the model."""
    monkeypatch.setattr(
        cc.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "clean\n", "warning noise\n"),
    )
    assert cc._run_shell("echo clean") == "clean\n"
