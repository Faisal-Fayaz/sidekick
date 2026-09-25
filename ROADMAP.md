# Sidekick Roadmap

Shared plan for [`Faisal01011/sidekick`](https://github.com/Faisal01011/sidekick) —
the single source of truth for what we're building and in what order.
The short `## Roadmap` section in `README.md` is just a pointer here.

> **Rule: this file changes by pull request only.** Either maintainer
> ([Faisal01011](https://github.com/Faisal01011) or
> [Irfanwani](https://github.com/Irfanwani)) may propose; either may approve.
> Keep each versioned section to ≤4 items so it stays a real plan, not a wishlist.
> Workflow details: [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Vision

A local-first terminal companion you can talk to — chat, voice, and tools,
on your hardware. No cloud account required, no API bill by default.

## Principles

How we decide what gets in:

- **Offline-first.** Works fully offline via Ollama; network features degrade gracefully.
- **Small-model-friendly.** Deterministic grounding (injected facts) over prompt instructions — small local models ignore rules but can't argue with facts.
- **Zero-dep bias.** Prefer stdlib solutions (FTS5 over vectors, `argparse`-grade simplicity) so it runs on a 4GB box.
- **Safety gates before features.** Writes need approval, destructive patterns hard-refused, SSRF guards on fetchers. New capabilities ship with regression tests (`tests/test_security.py`).

## Next — v0.12.0 (daemon compounds)

1. **Scheduled tasks (#38)** — natural-language schedules for the daemon, persisted on systemd + launchd, deliveries respect DND.
2. **Background runs via `sk run --bg` (#39)** — daemon + desktop notification on completion, reusing the `--json` envelope; spend-cap gated.
3. **Session allowlist (#40)** — per-session auto-approve list (`--allow`), safety gate for unattended runs.
4. **Daemon-as-teammate pilot (#41)** — morning digest only (brief + dirty repos + failures); full CI watch out of scope.

## Later

Parking lot — real ideas, no version attached. Promote to `Next` by PR.
Thesis: own the users cloud agents structurally can't serve
(offline, regulated, no-API-bill) — don't chase their feature list.

- **Air-gapped install** — offline bundle (vendored wheels + model docs) for locked-down environments. The #1 enterprise gate.
- **Team memory + shared skills** — org-wide `SKILL.md` packs and memory with permissions. Where seats and revenue appear.
- **Thin IDE extension** — VS Code client talking to the local daemon. Same offline brain, meets users where they live.
- **Daemon as a teammate** — scheduled briefs, dirty-repo/CI watch, morning digests. The junior dev who never sleeps and never leaks code.
- **Distilled sidekick-optimized small model** — on-device trajectories as training signal nobody else can see; greatness on 4GB VRAM as a compounding edge.
- **Managed enterprise flavor** — SSO, per-team tool policies, audit dashboards on top of the same binary. Open core stays free.
- **Background runs** — `sk run --bg` with daemon + desktop notification on completion. Stepping stone to daemon-as-teammate.
- **User-defined tools** — custom local tools declared in config/skill files without touching code. Compounds the skills story.
- **Pluggable search** — Serper/Brave BYO-key + local searxng + real page extraction; keyless DDG stays the default.
- **Richer memory** — recency/importance decay, dedup/merge, proactive `remember` proposals, pluggable embeddings (FTS5 stays the floor).
- **Python SDK + localhost HTTP API** — library and CLI from one core, so others can build on sidekick.
- **Keyring + spend caps** — OS keyring backend with file fallback; per-session cost display for BYO-key users. (Shipped `v0.10.0`, closes #30.)
- **Opt-in crash telemetry** — no data by default, explicit flag only.
- **Test-infra remainder** — pre-commit config, property-based allowlist/SSRF tests, coverage gates. (Rides `v0.11.0`, closes #35.)
- **Daemon remainder** — launchd unit (macOS) + desktop notifications with do-not-disturb. (Rides `v0.11.0`, closes #34.)
- **TUI overhaul, tranche B2** — approval cards + streaming Markdown (A1/A2/B1 shipped in v0.6.0).
- **Anthropic streaming** — SSE token streaming in `anthropic_backend`; closes the documented non-streaming v1 gap with the OpenAI path.
- **Thinking display** — surface Claude thinking blocks and qwen3 reasoning uniformly in the TUI. Transparency users already get half of.
- **`doctor --fix` + `sk report`** — auto-remediation (pull missing model, create config) plus a diagnostics bundle for support. From the original audit, never parked.
- **Skill registry** — discover installable packs beyond superpowers. `skills-search` covers installed; this covers the universe.
- **Scheduled tasks** — natural-language schedules for the daemon ("brief me every morning"). Stepping stone to daemon-as-teammate.
- **Session allowlist** — per-session auto-approve list (`--allow`). Safety customization between yolo and per-prompt confirms.
- **Release policy** — minor vs patch rules, changelog generation, pre-release testing checklist. Three releases so far have been ad hoc.
- **Testing strategy** — property-based allowlist/SSRF tests, coverage gates, golden-file eval growth. Turns the 300+-test suite into a system.
- **Security threat model** — documented adversary model (shell gating, SSRF, prompt injection) + what a future audit should probe.
- **Performance plan** — latency/token budgets per surface, benchmark harness, perf regression tests. No numbers exist anywhere today.
- **Plugin ecosystem design** — manifest format, entrypoints, sandboxing for user-defined tools. The detailed design behind the bullet.
- **Contributor growth** — good-first-issue curation, onboarding path, review SLAs. For turning 2 maintainers into 3+.
- **Sustainability options** — licensing, seats vs support, what stays open-core if enterprise flavor ever happens. Early thinking, no commitments.

Explicit non-goals: a cloud-hosted version, frontier feature parity, native mobile apps.

## Done

Shipped, most recent first. Details in [Releases](https://github.com/Faisal01011/sidekick/releases).

- `v0.11.0` — daemon remainder (#34: launchd + notifier with DND) + test-infra remainder (#35: pre-commit, property tests, coverage).
- `v0.10.0` — bundle release: `sk run --json`, `/fork`, `sk models pull/prune`, capability profiles, `sk stats`, MCP server + keyring/spend caps (#30).
- `v0.9.0` — mac audio capture fix (contributor branch).
- `v0.8.0` — copy text styles fix (contributor branch).
- `v0.7.0` — text-JSON tool fallback for models without native tool calling, MCP server milestone docs.
- `v0.6.0` — TUI reskin: dual dark/light themes, role rework, keymap rationalization, generated F1 help, status bar, sessions drawer.
- `v0.5.0` — parallel tool dispatch, Anthropic prompt caching, plan-review gate, rolling session compaction (schema v4).
- `v0.4.0` — per-project `.sidekick.toml` + memory namespaces (schema v3), `sk export` transcripts, `sk upgrade` self-update, `sk init` wizard, macOS cwd-discovery test fix.
- `v0.3.0` — `sk audit` + `tool_runs` schema v2, native Anthropic provider, mypy cleanup + tighter baseline, connect picker fix, API error surfacing, bare-`sk` TUI default, `tui/` split.
- `v0.1.2` — release-job fix (checkout before tagging).
- `v0.2.0` — skills search, systemd `daemon-install`, `commands/` split, format gate.
- `v0.1.1` — PyPI trusted publishing (`sidekick-agent`), AUR + conda-forge notes.
- Voice input (local STT) · Skills (superpowers) · Sessions · Providers/BYOK · Eval harness.
- Quality pass: security regression suite, lint/type CI (3.12–3.14 × ubuntu/macos), single-source model map, `tools/` + `cli/` package splits, DB `user_version` migrations.
