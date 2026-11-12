"""`sk fix`: the guided fix loop, and what it reports (#451).

The point of the command is the verdict: a change that does not pass the
verification must not ship as "done", and a verification that could not run
must not be reported as a pass.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from sk.cli import app
from sk.config import Config


def _cfg():
    return Config(provider="ollama", model="fake", base_url="http://x/v1", api_key="x")


def _report(status: str = "pass", *, repairable: bool = False, note: str = ""):
    """A verification result, the shape `verify_files` returns."""
    from sk.check.runner import CheckResult
    from sk.verify import VerificationResult

    return VerificationResult(
        status=status,
        results=(
            CheckResult(
                id="syntax-python",
                command="python -c compile",
                kind="syntax",
                parser="raw",
                status=status,
                exit_code=0 if status == "pass" else 1,
                duration=0.1,
                note=note,
            ),
        ),
    )


def _patch(monkeypatch, *, turns, verdicts):
    """Patch the agent turn. The verification verdict arrives through the sink.

    The agent owns the repair loop (so a repair round keeps the conversation's
    context), and the command learns the verdict from the sink the agent fills.
    """
    import sk.agent

    seen = {"prompts": [], "approvers": [], "sinks": []}

    def fake_run_agent(prompt, history, cfg, **kwargs):
        seen["prompts"].append(prompt)
        seen["approvers"].append(kwargs.get("approve"))
        sink = kwargs.get("verify_sink")
        seen["sinks"].append(sink)
        turn = turns[min(len(seen["prompts"]) - 1, len(turns) - 1)]
        on_tool = kwargs.get("on_tool")
        if on_tool is not None:
            for name, path in turn.get("edits", []):
                on_tool(name, {"path": path}, "ok")
        if sink is not None and turn.get("edits"):
            # The agent only verifies what it changed; nothing edited means no
            # verdict at all.
            verdict = verdicts[min(len(seen["prompts"]) - 1, len(verdicts) - 1)]
            sink["status"] = verdict.status
            sink["report"] = verdict
            sink["repairs_used"] = 1 if verdict.status == "fail" else 0
        return turn.get("answer", "done")

    monkeypatch.setattr(sk.agent, "run_agent", fake_run_agent)
    return seen


def test_the_command_exists():
    result = CliRunner().invoke(app, ["fix", "--help"])
    assert result.exit_code == 0, result.output
    assert "prove the fix works" in result.output


def test_a_passing_verification_exits_zero_and_reports_the_verdict(monkeypatch):
    import sk.cli.commands.fix as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    _patch(
        monkeypatch,
        turns=[{"answer": "fixed it", "edits": [("write_file", "app.py")]}],
        verdicts=[_report("pass")],
    )

    result = CliRunner().invoke(app, ["fix", "the parser drops the last token"])
    assert result.exit_code == 0, result.output
    assert "fixed it" in result.output
    assert "verification: pass" in result.output


def test_a_failing_verification_does_not_ship_as_done(monkeypatch):
    import sk.cli.commands.fix as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    _patch(
        monkeypatch,
        turns=[{"answer": "I think I fixed it", "edits": [("write_file", "app.py")]}],
        verdicts=[_report("fail", repairable=True, note="SyntaxError")],
    )

    result = CliRunner().invoke(app, ["fix", "the parser drops the last token"])
    assert result.exit_code == 1, result.output
    assert "verification: fail" in result.output


def test_an_unknown_verification_is_reported_and_not_a_pass(monkeypatch):
    import sk.cli.commands.fix as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    _patch(
        monkeypatch,
        turns=[{"answer": "fixed", "edits": [("write_file", "app.py")]}],
        verdicts=[_report("unknown", note="missing tool: ruff")],
    )

    result = CliRunner().invoke(app, ["fix", "whatever"])
    assert result.exit_code == 1, result.output
    assert "verification: unknown" in result.output


def test_the_verdict_comes_from_the_agent_gate(monkeypatch):
    """The agent owns the repair loop — a repair round keeps the context — and
    the command learns the verdict from the sink the agent fills."""
    import sk.cli.commands.fix as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    seen = _patch(
        monkeypatch,
        turns=[{"answer": "fixed", "edits": [("write_file", "app.py")]}],
        verdicts=[_report("pass")],
    )

    result = CliRunner().invoke(app, ["fix", "the parser drops the last token"])
    assert result.exit_code == 0, result.output
    assert seen["sinks"] and seen["sinks"][0] is not None, "the agent was not asked for a verdict"
    assert "verification: pass" in result.output


def test_nothing_edited_skips_the_verification(monkeypatch):
    import sk.cli.commands.fix as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    _patch(
        monkeypatch,
        turns=[{"answer": "I could not find the cause", "edits": []}],
        verdicts=[_report("pass")],
    )

    result = CliRunner().invoke(app, ["fix", "something"])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output


def test_the_edited_files_are_reported(monkeypatch):
    import sk.cli.commands.fix as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    _patch(
        monkeypatch,
        turns=[
            {
                "answer": "fixed",
                "edits": [("write_file", "a.py"), ("edit_file", "b.py"), ("read_file", "c.py")],
            }
        ],
        verdicts=[_report("pass")],
    )

    result = CliRunner().invoke(app, ["fix", "whatever", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["edited"] == ["a.py", "b.py"]


def test_the_agent_works_under_the_approval_gate(monkeypatch):
    """The command must not widen the gate: the agent's writes still ask."""
    import sk.cli.commands.fix as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    seen = _patch(
        monkeypatch,
        turns=[{"answer": "fixed", "edits": [("write_file", "a.py")]}],
        verdicts=[_report("pass")],
    )

    CliRunner().invoke(app, ["fix", "whatever"])
    assert callable(seen["approvers"][0]), "the agent ran without an approver"


def test_the_json_report_carries_the_verdict(monkeypatch):
    import sk.cli.commands.fix as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    _patch(
        monkeypatch,
        turns=[{"answer": "fixed", "edits": [("write_file", "a.py")]}],
        verdicts=[_report("fail", repairable=True, note="SyntaxError")],
    )

    result = CliRunner().invoke(app, ["fix", "whatever", "--json"])
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert report["verdict"] == "fail"
    assert report["edited"] == ["a.py"]
    assert report["verification"]["status"] == "fail"


def test_the_checks_run_once(monkeypatch):
    """The agent owns the gate, so the checks run once — not once by the agent
    and again by the command."""
    import sk.cli.commands.fix as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    seen = _patch(
        monkeypatch,
        turns=[{"answer": "fixed", "edits": [("write_file", "a.py")]}],
        verdicts=[_report("pass")],
    )

    CliRunner().invoke(app, ["fix", "whatever"])
    assert len(seen["prompts"]) == 1, "the command ran a second agent turn"
