# Contributing to Sidekick

How to pick up work and get it merged. The shared plan lives in
[`ROADMAP.md`](ROADMAP.md); this file is the workflow around it.

## Picking up work

1. Browse [open issues](https://github.com/Faisal01011/sidekick/issues) —
   they're grouped by [milestones](https://github.com/Faisal01011/sidekick/milestones)
   (`v0.3.0` = now; later work parks as bullets in `ROADMAP.md`).
2. Comment on the issue so we don't duplicate effort, then work from a
   branch (`feature/<what>` or `fix/<what>`).
3. Starting work? Assign the issue to yourself *before* coding — the
   assignee field is the lock between maintainers. Unassign if you stop.
3. Want something not listed? Open an issue first (or a PR against
   `ROADMAP.md` — the plan changes by PR, either maintainer may approve).

## Development setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Faisal01011/sidekick && cd sidekick
uv tool install -e ".[voice]"   # editable dev install as `sk`, STT included
sk doctor                        # checks provider + model (needs `ollama serve`)
```

## Before opening a PR

```bash
uv run --with pytest --with hypothesis pytest tests -q   # full suite (387 tests, no Ollama needed)
uvx ruff check src tests               # lint baseline (E/F/I/UP, see pyproject)
uvx mypy src/sk/tools src/sk/agent.py src/sk/router.py src/sk/config.py src/sk/store.py
```

Optional local gates (mirrored in CI):

```bash
pipx install pre-commit && pre-commit install   # ruff + mypy + fast subset on commit
uv run --with pytest pytest tests -q --cov=sk --cov-report=term-missing  # coverage report (informational)
```

Notes:

- Tests must never touch your live `~/.sidekick/` — the suite-wide fixture in
  `tests/conftest.py` isolates config + history DB. Keep it that way.
- New capabilities that touch `shell`, writes, or fetchers need regression
  tests in `tests/test_security.py` (destructive patterns, blocklists, SSRF).
- Keep the test-count badge in `README.md` in sync (`pytest --collect-only`);
  CI fails the build if it drifts.
- Pure refactors ship with zero test edits (green suite without touching
  `tests/` proves no behavior change).

## PR conventions

- One concern per PR; small diffs get reviewed fastest.
- Link work: `closes #N` in the body so merging closes the issue and the
  milestone tracks itself.
- Either maintainer ([Faisal01011](https://github.com/Faisal01011),
  [Irfanwani](https://github.com/Irfanwani)) may approve and merge.

## Releases (read before bumping the version)

Publishing is automatic and merge-driven (`.github/workflows/release.yml`):
merging to `main` of the canonical repo with a bumped `__version__` in
`src/sk/__init__.py` trusted-publishes `sidekick-agent` to PyPI and cuts a
GitHub Release. So **don't bump the version in a regular feature PR** — bump
it only in the PR that is meant to become a release.
