"""Repo-root pytest configuration.

Only options live here. pytest only accepts `pytest_addoption` from the
rootdir conftest, and the golden suite needs one flag: `--update-goldens`
rewrites the files in `tests/golden/` from the current behaviour, so a golden
change lands in the same commit as the behaviour change it records (#112).
"""

from __future__ import annotations


def pytest_addoption(parser):
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="rewrite the golden files in tests/golden/ from the current behaviour",
    )
