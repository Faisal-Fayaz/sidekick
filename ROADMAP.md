# Sidekick Roadmap

Shared plan for [`Faisal01011/sidekick`](https://github.com/Faisal01011/sidekick) —
the single source of truth for what we're building and in what order.
The short `## Roadmap` section in `README.md` is just a pointer here.

> **Rule: this file changes by pull request only.** Either maintainer
> ([Faisal01011](https://github.com/Faisal01011) or
> [Irfanwani](https://github.com/Irfanwani)) may propose; either may approve.
> Keep `Now` to ≤4 items so it stays a real plan, not a wishlist.

## Vision

A local-first terminal companion you can talk to — chat, voice, and tools,
on your hardware. No cloud account required, no API bill by default.

## Principles

How we decide what gets in:

- **Offline-first.** Works fully offline via Ollama; network features degrade gracefully.
- **Small-model-friendly.** Deterministic grounding (injected facts) over prompt instructions — small local models ignore rules but can't argue with facts.
- **Zero-dep bias.** Prefer stdlib solutions (FTS5 over vectors, `argparse`-grade simplicity) so it runs on a 4GB box.
- **Safety gates before features.** Writes need approval, destructive patterns hard-refused, SSRF guards on fetchers. New capabilities ship with regression tests (`tests/test_security.py`).

## Now → `v0.2.0`

GitHub milestone: [`v0.2.0`](https://github.com/Faisal01011/sidekick/milestones).

- [ ] `sk skills search` — search installed skill packs by keyword (packs exist, discovery doesn't).
- [ ] Daemon as a systemd service — `sk daemon` runs foreground/`--once` today; add unit file + `sk daemon-install`.
- [ ] Finish `commands/` split — commands still live in `cli/__init__.py` after the pure-move split (`cli/base.py`, `cli/approvers.py`, `cli/resolve.py` done); move command groups into `cli/commands/`.
- [ ] `ruff format` pass (32 files) + re-enable the format gate in CI (dropped in the quality pass because the repo isn't format-clean yet).

## Next → `v0.3.0`

GitHub milestone: [`v0.3.0`](https://github.com/Faisal01011/sidekick/milestones).

- [ ] Spoken replies (offline TTS) — voice input (`sk talk`, faster-whisper) is done; replies are still text-only.
- [ ] Native Anthropic provider — Claude reachable today only via OpenRouter; wrap the Anthropic-native API.
- [ ] `mypy` cleanup — 16 pre-existing errors in `slash.py` / `tui.py` / `cli/__init__.py`, then tighten the CI baseline toward strict.

## Later

Parking lot — real ideas, no version attached. Promote to `Next` by PR.

- (empty — propose via PR)

## Done

Shipped, most recent first. Details in [Releases](https://github.com/Faisal01011/sidekick/releases).

- `v0.1.2` — release-job fix (checkout before tagging).
- `v0.1.1` — PyPI trusted publishing (`sidekick-agent`), AUR + conda-forge notes.
- Voice input (local STT) · Skills (superpowers) · Sessions · Providers/BYOK · Eval harness.
- Quality pass: security regression suite, lint/type CI (3.12–3.14 × ubuntu/macos), single-source model map, `tools/` + `cli/` package splits, DB `user_version` migrations.
