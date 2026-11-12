"""The live eval task set is checked offline (#283).

The harness itself (`docs/probe-evals.py`) spends model time and money, so it
is not part of `pytest`. But the task set it reads is: a task set that rots —
a prompt that no longer applies, an expectation that can never be met — makes
the live numbers meaningless, and nobody notices until the leaderboard is
wrong.

These tests keep the set honest without running a model.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TASKS_PATH = ROOT / "docs" / "eval" / "tasks.json"
HARNESS_PATH = ROOT / "docs" / "probe-evals.py"

KINDS = {"chat", "tool", "network"}


def _harness():
    """Import the harness script by path (it lives in docs/, not the package)."""
    spec = importlib.util.spec_from_file_location("probe_evals", HARNESS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tasks() -> list[dict]:
    return json.loads(TASKS_PATH.read_text())["tasks"]


def test_task_set_is_valid():
    raw = json.loads(TASKS_PATH.read_text())
    assert raw["schema"] == 1
    tasks = raw["tasks"]
    assert tasks, "the task set is empty"
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)), "duplicate task ids"
    for task in tasks:
        assert task["id"].strip(), "a task has no id"
        assert task["prompt"].strip(), f"{task['id']} has no prompt"
        assert task["kind"] in KINDS, f"{task['id']} has an unknown kind {task['kind']!r}"
        assert task["why"].strip(), f"{task['id']} does not say what it probes"
        has_expect = bool(task.get("expect"))
        has_from = bool(task.get("expect_from"))
        assert has_expect or has_from, f"{task['id']} expects nothing"


def test_task_set_covers_the_seeds():
    """The seeds the issue names: a greeting, a short answer, a grounding
    one-liner, a tool turn, and a network task."""
    kinds = {t["kind"] for t in _tasks()}
    assert kinds == KINDS, f"the task set lost a kind: {kinds}"
    ids = " ".join(t["id"] for t in _tasks())
    for seed in ("p1_greeting", "p2_", "p3_grounding", "p4_tool_turn", "p5_network"):
        assert seed in ids, f"the seed task {seed} is gone"


def test_the_network_task_is_isolated():
    """A task that fetches must be marked, so it is never run by accident."""
    network = [t for t in _tasks() if t["kind"] == "network"]
    assert network, "there is no network task to isolate"
    for task in network:
        assert task["id"].startswith("p5_"), task["id"]


def test_tool_tasks_name_the_tool_they_need():
    """A tool task that does not say which tool it needs cannot be judged."""
    for task in _tasks():
        if task["kind"] == "tool":
            assert task.get("tool"), f"{task['id']} is a tool task with no tool"


def test_verdict_rules():
    """OBEYED / PARAPHRASED / FAILED, the probe-routing verdict style."""
    module = _harness()
    verdict = module.verdict
    assert verdict("the answer is banana", ["banana"]) == "OBEYED"
    assert verdict("BANANA!", ["banana"]) == "OBEYED"
    assert verdict("banana split", ["banana", "yellow"]) == "PARAPHRASED"
    assert verdict("something else", ["banana"]) == "FAILED"
    # A task that needed a tool and never called it is FAILED, whatever it said.
    assert verdict("banana", ["banana"], tool_ran=False) == "FAILED"
    assert verdict("", [], tool_ran=True) == "OBEYED"


def test_harness_loads_the_committed_task_set():
    module = _harness()
    tasks = module.load_tasks()
    assert [t["id"] for t in tasks] == [t["id"] for t in _tasks()]


def test_render_emits_the_three_tables():
    module = _harness()
    results = [
        {
            "id": "p1_greeting",
            "kind": "chat",
            "verdict": "OBEYED",
            "seconds": 0.5,
            "ttft": 0.1,
            "tokens": 12,
            "tools": [],
            "answer": "hi",
        }
    ]
    out = module.render(results, price_per_mtok=0.5)
    assert "Speed" in out and "Quality" in out and "Cost" in out
    assert "1 obeyed" in out


def test_the_task_set_is_not_in_pytest_collection():
    """The harness spends model time; it must not be collected as a test."""
    assert not HARNESS_PATH.name.startswith("test_")
    assert HARNESS_PATH.parent.name == "docs"
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert 'testpaths = ["tests"]' in pyproject


@pytest.mark.parametrize("path", [TASKS_PATH, HARNESS_PATH, ROOT / "docs" / "eval" / "README.md"])
def test_the_eval_files_exist(path):
    assert path.is_file(), f"{path} is missing"
