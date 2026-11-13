"""`sk explain`: a grounded explanation, and a read-only one (#461).

Two things have to hold: the answer is asked for with citations and an
explicit "the code does not say" escape, and the run cannot reach a tool that
writes.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from sk.cli import app
from sk.config import Config


def _cfg():
    return Config(provider="ollama", model="fake", base_url="http://x/v1", api_key="x")


def _patch(monkeypatch, answer: str = "src/app.py:12 parses the header."):
    import sk.agent

    seen: dict = {}

    def fake_run_agent(prompt, history, cfg, **kwargs):
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return answer

    monkeypatch.setattr(sk.agent, "run_agent", fake_run_agent)
    return seen


def test_the_command_exists():
    result = CliRunner().invoke(app, ["explain", "--help"])
    assert result.exit_code == 0, result.output
    assert "grounded" in result.output.lower()


def test_the_run_is_read_only(monkeypatch):
    """No approval-gated tool is reachable: nothing here can write."""
    import sk.cli.commands.explain as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    seen = _patch(monkeypatch)

    result = CliRunner().invoke(app, ["explain", "how does the parser work?"])
    assert result.exit_code == 0, result.output
    assert seen["kwargs"]["read_only"] is True


def test_the_prompt_asks_for_citations_and_for_an_honest_no(monkeypatch):
    import sk.cli.commands.explain as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    seen = _patch(monkeypatch)

    CliRunner().invoke(app, ["explain", "how does the parser work?"])
    prompt = seen["prompt"]
    assert "file:line" in prompt
    assert "does not answer the question" in prompt
    assert "not from memory" in prompt
    assert "how does the parser work?" in prompt


def test_the_answer_is_printed(monkeypatch):
    import sk.cli.commands.explain as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    _patch(monkeypatch, answer="src/app.py:12 parses the header.")

    result = CliRunner().invoke(app, ["explain", "what parses the header?"])
    assert result.exit_code == 0, result.output
    assert "src/app.py:12" in result.output


def test_the_json_report_carries_the_question_and_the_answer(monkeypatch):
    import sk.cli.commands.explain as mod

    monkeypatch.setattr(mod, "_cfg", _cfg)
    _patch(monkeypatch, answer="src/app.py:12 parses the header.")

    result = CliRunner().invoke(app, ["explain", "what parses the header?", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["question"] == "what parses the header?"
    assert "src/app.py:12" in report["answer"]
