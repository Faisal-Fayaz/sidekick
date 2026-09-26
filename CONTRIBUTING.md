# Contributing to Sidekick

How to pick up work and get it merged. The shared plan lives in
[`ROADMAP.md`](ROADMAP.md); this file is the workflow around it.

## Picking up work

1. Browse [open issues](https://github.com/Faisal-Fayaz/sidekick/issues) —
   they're grouped by [milestones](https://github.com/Faisal-Fayaz/sidekick/milestones)
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
git clone https://github.com/Faisal-Fayaz/sidekick && cd sidekick
uv tool install -e ".[voice]"   # editable dev install as `sk`, STT included
sk doctor                        # checks provider + model (needs `ollama serve`)
```

## First contribution (onboarding path)

New here? Do this in order — each step proves the next one works:

1. **Setup** (10 min): clone, install, `sk doctor`. If doctor is red,
   `sk doctor --fix` repairs the common cases; otherwise paste
   `sk report` into a new issue.
2. **First test run** (5 min): `uv run --with pytest --with hypothesis pytest
   tests/test_entry.py -q` — pick any single test file; the suite never needs
   Ollama or network (suite-wide fixture isolates `~/.sidekick/`).
3. **First PR**: pick an issue labeled [`good first issue`][gfi] (e.g. the
   README command-table audit — mechanical, fully specified). Keep it to one
   concern, run the gates in "Before opening a PR", open the PR with
   `closes #N`.

[gfi]: https://github.com/Faisal-Fayaz/sidekick/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22

## Review SLAs

Between maintainers ([Faisal-Fayaz](https://github.com/Faisal-Fayaz),
[Irfanwani](https://github.com/Irfanwani)):

- **First response** (acknowledge / assign / close): within 48h.
- **Review verdict** (approve or request changes): within 5 days.
- **Docs/test-only PRs**: either maintainer may merge once CI is green and
  the PR is 24h old with no objection.
- Stale reviews get a nudge comment; still stale after 7 days, the other
  maintainer may merge green PRs unilaterally. Either maintainer may propose
  tightening or loosening these numbers by PR.

## Before opening a PR

```bash
uv run --with pytest --with hypothesis pytest tests -q   # full suite (466 tests, no Ollama needed)
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
- Either maintainer ([Faisal-Fayaz](https://github.com/Faisal-Fayaz),
  [Irfanwani](https://github.com/Irfanwani)) may approve and merge.

## Releases (read before bumping the version)

Publishing is automatic and merge-driven (`.github/workflows/release.yml`):
merging to `main` of the canonical repo with a bumped `__version__` in
`src/sk/__init__.py` trusted-publishes `sidekick-agent` to PyPI and cuts a
GitHub Release. So **don't bump the version in a regular feature PR** — bump
it only in the PR that is meant to become a release.

### Version policy

- **patch** (`0.17.0` → `0.17.1`): fixes, docs, tests, packaging, CI — no new
  user-facing behavior.
- **minor** (`0.17.0` → `0.18.0`): new features, new commands/flags, behavior
  changes. The normal release train.
- **major** (`1.0`): reserved for breaking changes (config format, CLI
  removals, protocol breaks). Requires an explicit maintainer decision.
- Never skip or reuse a number: every bump publishes (or fails loudly).
  `v0.14.0` was tagged in code but never reached PyPI — that gap is now a
  documented anti-pattern, not a procedure.

### Merge subjects feed the changelog

GitHub auto-generates release notes from merge subjects, so write them as
`area: what changed`: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`,
`test:`. One concern per PR keeps each line readable.

### Pre-release checklist

- [ ] Version bumped on a `release/*` branch only (CI enforces this).
- [ ] `ROADMAP.md` `Done` records the release, `Next` points past it.
- [ ] Test-count badge in `README.md` matches `pytest --collect-only`.
- [ ] Full CI green on the release PR (lint, mypy, tests × 6, badge, build).
- [ ] After merge: release workflow success, PyPI shows the version,
      GitHub Release exists with artifacts.
