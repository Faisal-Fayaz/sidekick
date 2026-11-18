"""Prompt-overhead budgets: an accidental bloat fails the build (#499)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import sk.perf as perf

# The probe is a script with a hyphenated name, not a package module.
_spec = importlib.util.spec_from_file_location(
    "probe_perf", Path(__file__).resolve().parents[1] / "docs" / "probe-perf.py"
)
assert _spec and _spec.loader
probe_perf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe_perf)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    import sk.store as store
    import sk.team as team

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(team, "CONFIG_DIR", tmp_path / "empty-home" / ".sidekick")
    return tmp_path


def test_every_surface_is_within_budget(isolated):
    rows = perf.measure_all()
    assert perf.check(rows) == []
    assert len(rows) == len(perf.PROMPTS) + 1


def test_component_overhead_is_pinned(isolated):
    rows = perf.measure_all()
    assert max(r["system"] for r in rows) <= 2300
    assert max(r["tools"] for r in rows) <= 2950
    assert max(r["fixed"] for r in rows) <= 5200


def test_history_does_not_blow_the_total(isolated):
    row = perf.measure_surface("chat", history_pairs=perf.HISTORY_PAIRS)
    assert row["total"] <= perf.COMPONENT_BUDGETS["with_history"]
    assert row["history"] > 0


def test_an_unknown_surface_is_named(isolated):
    with pytest.raises(KeyError, match="unknown surface"):
        perf.measure_surface("nope")


def test_a_bloated_surface_is_reported(isolated, monkeypatch):
    rows = perf.measure_all()
    rows[0] = {**rows[0], "fixed": rows[0]["budget"] + 1}
    problems = perf.check(rows)
    assert any(rows[0]["surface"] in p and "budget" in p for p in problems)


def test_the_harness_exits_zero_then_one_on_breach(isolated, monkeypatch, capsys):
    assert probe_perf.main([]) == 0
    assert "All surfaces within budget" in capsys.readouterr().out

    original = dict(perf.PROMPTS)
    monkeypatch.setitem(perf.PROMPTS, "chat", ("x" * 40_000, {}))
    try:
        assert probe_perf.main([]) == 1
        out = capsys.readouterr().out
        assert "BUDGET BREACHES" in out and "chat" in out
    finally:
        perf.PROMPTS.clear()
        perf.PROMPTS.update(original)


def test_the_harness_has_json_output(isolated, capsys):
    assert probe_perf.main(["--json"]) == 0
    import json

    data = json.loads(capsys.readouterr().out)
    assert data["breaches"] == []
    assert len(data["rows"]) == len(perf.PROMPTS) + 1
