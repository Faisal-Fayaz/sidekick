"""Fixture-based parser tests for pytest, ruff, mypy, eslint, tsc, and go test.

Each fixture is real-shaped tool output plus the diagnostics a model should see.
An empty parse is never treated as a pass; the runner labels it unparsed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sk.check.parsers import parse_output

FIXTURES = sorted((Path(__file__).parent / "check" / "fixtures").glob("*/expected.json"))
PARSERS = ("pytest", "ruff", "mypy", "eslint", "tsc", "go-test")


@pytest.mark.parametrize(
    "expected_path",
    FIXTURES,
    ids=[path.parent.name for path in FIXTURES],
)
def test_parser_fixture(expected_path: Path):
    expected = json.loads(expected_path.read_text())
    parser = expected_path.parent.name
    text = (expected_path.parent / "output.txt").read_text()
    diagnostics = parse_output(
        parser,
        text,
        root=expected["root"],
        check=parser,
        exit_code=expected["exit_code"],
    )

    assert len(diagnostics) == len(expected["diagnostics"])
    for actual, wanted in zip(diagnostics, expected["diagnostics"]):
        assert actual.file == wanted["file"]
        assert actual.line == wanted["line"]
        assert actual.column == wanted["column"]
        assert actual.severity == wanted["severity"]
        assert actual.source == parser
        assert actual.check == parser
        if wanted["code"]:
            assert actual.code == wanted["code"]
        assert wanted["message_contains"] in actual.message


def test_every_required_parser_has_a_fixture():
    assert {path.parent.name for path in FIXTURES} == set(PARSERS)


@pytest.mark.parametrize("parser", PARSERS)
def test_parser_never_raises_on_garbage(parser: str):
    assert parse_output(parser, "\x00 not a real tool report\n", check="x") == ()
    assert parse_output(parser, "", check="x") == ()


def test_unknown_and_raw_parsers_return_empty():
    assert parse_output("raw", "anything", check="x") == ()
    assert parse_output("not-a-parser", "anything", check="x") == ()


def test_pytest_summary_only_falls_back_to_file_with_line_zero():
    text = "FAILED tests/test_x.py::test_one - boom\n1 failed in 0.01s\n"
    diagnostics = parse_output("pytest", text, check="test")
    assert len(diagnostics) == 1
    assert diagnostics[0].file == "tests/test_x.py"
    assert diagnostics[0].line == 0
    assert diagnostics[0].code == "test_one"
    assert "boom" in diagnostics[0].message


def test_pytest_collection_error_is_kept():
    text = "ERROR tests/test_import.py - ImportError: no module named app\n"
    diagnostics = parse_output("pytest", text, check="test")
    assert len(diagnostics) == 1
    assert diagnostics[0].file == "tests/test_import.py"
    assert "ImportError" in diagnostics[0].message


def test_ruff_would_reformat_is_a_diagnostic():
    diagnostics = parse_output("ruff", "Would reformat: src/app.py\n1 file would be reformatted\n")
    assert len(diagnostics) == 1
    assert diagnostics[0].file == "src/app.py"
    assert diagnostics[0].code == "format"
    assert diagnostics[0].line == 0


def test_eslint_json_format():
    text = json.dumps(
        [
            {
                "filePath": "/repo/src/a.js",
                "messages": [
                    {
                        "line": 4,
                        "column": 2,
                        "severity": 2,
                        "ruleId": "no-undef",
                        "message": "'x' is not defined",
                    },
                    {
                        "line": 8,
                        "column": 1,
                        "severity": 1,
                        "ruleId": "no-console",
                        "message": "Unexpected console statement",
                    },
                ],
            }
        ]
    )
    diagnostics = parse_output("eslint", text, root="/repo", check="lint")
    assert len(diagnostics) == 2
    assert diagnostics[0].file == "src/a.js"
    assert diagnostics[0].severity == "error"
    assert diagnostics[1].severity == "warning"


def test_go_test_json_format():
    lines = [
        {
            "Action": "output",
            "Package": "example.com/math",
            "Test": "TestAdd",
            "Output": "    math_test.go:12: got 3, want 4\n",
        },
        {"Action": "fail", "Package": "example.com/math", "Test": "TestAdd"},
        {"Action": "fail", "Package": "example.com/other"},
    ]
    text = "\n".join(json.dumps(line) for line in lines)
    diagnostics = parse_output("go-test", text, check="test")
    assert len(diagnostics) == 2
    assert diagnostics[0].file == "math_test.go"
    assert diagnostics[0].line == 12
    assert diagnostics[1].file == "example.com/other"
    assert diagnostics[1].line == 0


def test_absolute_paths_are_made_repo_relative():
    text = "/repo/src/app.py:3:1: F401 `os` imported but unused\n"
    diagnostics = parse_output("ruff", text, root="/repo", check="lint")
    assert diagnostics[0].file == "src/app.py"


def test_diagnostics_are_deduplicated():
    text = "src/a.py:1:1: F401 unused\nsrc/a.py:1:1: F401 unused\n"
    diagnostics = parse_output("ruff", text, check="lint")
    assert len(diagnostics) == 1
