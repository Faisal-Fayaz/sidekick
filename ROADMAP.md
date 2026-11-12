# Sidekick Roadmap

Shared plan for [`Faisal-Fayaz/sidekick`](https://github.com/Faisal-Fayaz/sidekick) —
the single source of truth for what we're building and in what order.
The short `## Roadmap` section in `README.md` is just a pointer here.

> **Rule: this file changes by pull request only.** Either maintainer
> ([Faisal-Fayaz](https://github.com/Faisal-Fayaz) or
> [Irfanwani](https://github.com/Irfanwani)) may propose; either may approve.
> Keep each versioned section to ≤4 items so it stays a real plan, not a wishlist.
> Workflow details: [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Vision

A local-first terminal agent for developers — chat, code intelligence, and
evidence-backed review on your hardware, with rules you can inspect and edit.
No cloud account required by default. Code review is the first developer
workstream; the assistant stays whole.

## Principles

How we decide what gets in:

- **Offline-first.** Works fully offline via Ollama; network features degrade gracefully.
- **Small-model-friendly.** Deterministic grounding (injected facts) over prompt instructions — small local models ignore rules but can't argue with facts.
- **Zero-dep bias.** Prefer stdlib solutions (FTS5 over vectors, `argparse`-grade simplicity) so it runs on a 4GB box.
- **Safety gates before features.** Writes need approval, destructive patterns hard-refused, SSRF guards on fetchers. New capabilities ship with regression tests (`tests/test_security.py`).
- **Additive developer features.** Review, verification, and later developer capabilities are packs on the shared harness, not a replacement for the assistant.

## Next — v0.35.0 (the developer ladder)

Next milestone; scope stays tentative until its issues are filed. Details live
in [`docs/developer-plan.md`](docs/developer-plan.md).

- **Guided change** — `sk fix`: a targeted fix loop with checkpoints and the verification gate (#451).
- **Project tests** — `sk test`: run the project's own tests through the check pipeline (#452).
- **Ship** — `sk pr`: prepare a pull request from the working tree, reviewed before push (#453).
- **Git helpers** — `git status`/`diff`/`commit` as first-class tools instead of prompted shell (#454).

Out of scope for now: IDE integration, the Python SDK, and OS-level sandbox
enforcement, which needs optional system packages (#440). Later versions
remain tentative.

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
- **Pluggable search** — Serper/Brave BYO-key + local searxng + real page extraction; keyless DDG stays the default.
- **Richer memory** — recency/importance decay, dedup/merge, proactive `remember` proposals, pluggable embeddings (FTS5 stays the floor).
- **Python SDK + localhost HTTP API** — library and CLI from one core, so others can build on sidekick.
- **Opt-in crash telemetry** — no data by default, explicit flag only.
- **Testing strategy** — property-based allowlist/SSRF tests, coverage gates, golden-file eval growth. Turns the 300+-test suite into a system.
- **Security threat model** — documented adversary model (shell gating, SSRF, prompt injection) + what a future audit should probe.
- **Performance plan** — latency/token budgets per surface, benchmark harness, perf regression tests. No numbers exist anywhere today.
- **Sustainability options** — licensing, seats vs support, what stays open-core if enterprise flavor ever happens. Early thinking, no commitments.
- **Website governance** — the Vite site in `website/` needs CI ownership (build + link check, preview deploys, named owner) or it rots.
- **Distribution health** — verify AUR and conda-forge install current releases; per-release install smoke test so channels stop silently rotting behind PyPI.
- **Windows decision** — CI is ubuntu+macos only and the code assumes POSIX. Either scope Windows support or declare it a non-goal explicitly.
- **Golden eval seed** — first golden-file tests for the eval harness: 3–5 locked prompt→reply pairs for the highest-traffic turns.
- **Compaction evals** — quality bar for rolling session summaries: faithfulness checks so long sessions don't silently degrade.
- **Registry trust + signing** — checksum pinning for remote packs (deferred from #48/#50): index carries hashes, installs verify before cloning, mismatch fails closed.
- **Backup + restore** — machine migration for `~/.sidekick` (history, memories, config, skills): single export/import path so a new laptop doesn't mean amnesia.
- **Docs freshness gate** — CI check locking the README command table to `--help` output, generalizing the badge-gate pattern.
- **Voice track** — STT quality bar, install-friction reduction, coverage for talk/mic paths. The only user surface with zero roadmap presence.
- **Release automation for AUR/conda** — auto-push both channels on release (Distribution health covers verifying; this closes the loop to publishing).
- **Quality tracking** — nightly model-quality scoreboard over a fixed eval set, tracked across releases.
- **Egress-proof compliance report (#156)** — tamper-evident proof-of-no-egress from the audit ledger (turns local-vs-egress rows into a sales document for regulated shops; pairs with air-gapped install).
- **Benchmark proof (#157)** — public fixed-task numbers: local 4GB vs cloud agents, with the $0 cost column. Makes "great on weak hardware" checkable instead of folklore.
- **Image/screenshot input (#178)** — attach a screenshot and ask what's wrong; `read_file` analog for images via local vision models.
- **Persistent shell sessions (#179)** — long-lived bash session (cwd/env/venv survive); opt-in beside one-shot `shell`.
- **JS-capable page extraction (#182)** — renderer-or-fallback chain for JS-heavy pages; SSRF/content caps unchanged.
- **Showcase gallery (#183)** — curated user transcripts (`sk export`) into `docs/showcase/`.
- **Public changelog narrative (#186)** — human what's-new per minor since 0.14.0, linked from README + site.
- **Benchmark leaderboard section (#188)** — empty table shell now, filled by #157 later.
- **Config profiles (#189)** — named `~/.sidekick/profiles/` switchable via flag.
- **Session search (#190)** — full-text search across past transcripts (FTS5 already in stack).

### Market-informed candidates (2026-11)

Market scan: JetBrains' 2026 survey of 15,000+ professional developers found
90% use coding agents weekly and 68% daily
([source](https://blog.jetbrains.com/research/2026/08/ai-coding-agent-adoption-2026/)).
The gap has moved from code generation to **intent, context, verification, and
evidence**. The candidates below target those gaps from Sidekick's local-first
angle. They are new — none repeat `Next`, the existing Later list, or Done — and
none are scheduled. Each would be an optional pack on the shared harness; none
changes the core loop, approvals, or audit. Promote one to `Next` by PR only
after it passes the promotion test in `docs/developer-plan.md` §5.1.

**Intent**

- **Issue-to-branch intake** — read a GitHub/Linear/Jira issue into a scoped
  task, then create a branch, plan, and draft PR through the planned git tools.
  Market: Linear Agent, Codex, and Copilot now start from assigned issues;
  Sidekick can do it without a hosted sandbox. Reads are automatic; writes and
  pushes stay approval-gated. This is intent intake, not a duplicate of the git
  primitives in `docs/developer-plan.md` §4.5.
- **Spec-to-verification trail** — durable `.sidekick/specs/` artifacts
  (requirements → design → tasks) that link each acceptance criterion to the
  checks and review findings that prove it. Market: Kiro specs and GitHub Spec
  Kit made spec-driven development mainstream; `/plan` is currently ephemeral.
  Local-first fit: artifacts are diffable, reviewable, and stay in the repo.

**Context**

- **Adaptive code retrieval** — add an LSP bridge (definitions, references,
  diagnostics, call graph) and an optional local semantic code index on top of
  `repo_map`/`search_code`, routing by task instead of replacing lexical search;
  optionally map multiple repos in a workspace. Market: JetBrains Context,
  Cursor, and Codex invest in repo context; recent LSP research shows semantic
  retrieval is task-dependent, so hybrid routing is the honest design. Separate
  from #282, which covers memory/skills retrieval, not code symbols.
  Local-first fit: on-disk index, local embeddings only, lexical fallback for
  4GB boxes.
- **Remote SSH workspace** — run the daemon/agent on a remote dev box while the
  local TUI keeps approvals, policy, and the audit ledger; no inbound ports and
  no cloud relay. Market: Codex Remote SSH and Claude remote control are now
  standard. This is self-hosted remote execution, not the cloud-hosted version
  ruled out below.

**Verification**

- **Browser verification pack** — drive a local Playwright/Chromium session to
  verify a web change, capture an accessibility snapshot or screenshot, and
  optionally emit an e2e test; egress stays deny-by-default. Market: Playwright
  MCP/CLI is becoming the default way agents see UI changes. Optional pack:
  detect an existing browser install, add no required dependency. Distinct from
  the read-only JS page extraction shipped in v0.25.
- **Docs co-change guard** — detect code changes that invalidate docs/ADRs and
  propose a same-PR docs update; start with changed public APIs, CLI help, and
  config keys. Market: a 2026 study of 29,669 AI-authored PRs found 63.2%
  changed code without docs; OpenAI runs a doc-gardening agent. Extends the
  existing Docs freshness gate beyond the README command table, and proposes
  rather than auto-generating to avoid documentation sprawl.
- **Taint-aware untrusted-content policy** — label content by origin (web,
  issue, repo file, tool output) and require approval before untrusted text can
  trigger writes, network egress, or secret-adjacent actions; record the
  decision in the ledger. Market: prompt injection is the top agent risk;
  Microsoft FIDES and recent coding-agent research show provenance beats
  prompt-only defenses. Runtime complement to the Security threat model, not a
  replacement for sandboxing.

**Evidence**

- **Review feedback memory** — learn local, inspectable rules from
  accepted/dismissed findings and PR replies, with a reviewable diff and easy
  rollback; protected rules can never be disabled automatically. Market: Cursor
  Bugbot's learned rules and Greptile's feedback learning show review quality is
  becoming a learning loop. Extends the editable review pack; feedback stays on
  disk.
- **Local run traces + replay** — a read-only timeline of model calls, tool
  calls, diffs, approvals, tokens/latency, and stop reason; export as HTML/JSON
  and replay a recorded run against a changed harness. Market: agent
  observability is now a category (OpenTelemetry traces, local tools). The audit
  ledger records tool runs; this adds the full run and replay. Traces stay in
  `~/.sidekick`, redacted by default; replay never calls the network unless
  asked.

Scan sources: [JetBrains 2026 survey](https://blog.jetbrains.com/research/2026/08/ai-coding-agent-adoption-2026/),
[Claude Code extension model](https://code.claude.com/docs/en/features-overview),
[Codex CLI](https://developers.openai.com/codex/cli),
[Kiro specs](https://kiro.dev/docs/specs/),
[GitHub Spec Kit](https://github.com/github/spec-kit),
[JetBrains Context](https://blog.jetbrains.com/ai/2026/07/introducing-jetbrains-context-repository-intelligence-for-coding-agents),
[Playwright MCP](https://playwright.dev/mcp/introduction),
[Bugbot learned rules](https://cursor.com/blog/bugbot-learning),
[docs-drift study](https://arxiv.org/pdf/2610.27381), and the
[prompt-injection SoK](https://arxiv.org/abs/2601.17548).

Explicit non-goals: a cloud-hosted version, frontier feature parity, native mobile apps.

## Done

Shipped, most recent first. Details in [Releases](https://github.com/Faisal-Fayaz/sidekick/releases).

- `v0.34.0` — **evals, benchmark, adoption.** A golden-file suite locks the turns people actually see — the help reply, the grounding block, the refusals, the tool-call fallback, the review prompt — with volatile values masked so a golden cannot rot overnight (#112). A live harness runs the fixed task set against real models and reports speed, quality and cost, with pass@k over repeated samples (#283). The benchmark is published: on a 4 GB box the small local tiers all score pass@1 0.50, and the two tool tasks fail because the models answer without acting — which is exactly what an evidence-checked verdict is for (#157). The leaderboard is on the site and in the README, and a test keeps the two copies in agreement (#188). A release smoke test now installs the published artifact, runs one offline turn, and checks the AUR pin against what PyPI published (#443). Tests 1845 → 1878.
- `v0.33.0` — **provider + platform hardening.** One agent loop now drives every provider: `sk.backends` is the seam (`Turn`, `ToolCall`, `Backend`), the Anthropic path keeps only the wire format, and a parity table runs the same scenarios against both — so a feature can no longer land on one side only (#312). CI gained a required `scan` job: `pip-audit` over the locked dependency set and a bandit gate, with the two high-severity findings fixed rather than skipped (#271). A declarative sandbox (`~/.sidekick/sandbox.toml`) denies reads outside the declared roots and network access from the shell, enforced in the tool layer and checked before the approval prompt, with the #288 bypass corpus pinned as a test (#329). Tests 1761 → 1845.
- `v0.32.0` — **editable review pack + verification.** Review policy is now layered built-in < user global < project < CLI, the eight `security/*` rules are protected from project overrides, and `--explain`/`--check-config` report the effective pack and its deterministic hash (#420). Ten built-in rules (eight security, two correctness) ship with positive/negative fixtures and seeded corpus cases (#421). `sk check` runs trust-gated `.sidekick/checks.toml` through the normal approval and audit path, parses pytest/ruff/mypy/eslint/tsc/`go test`, and reports timeouts, denials, and missing tools as `unknown` — never `pass` (#422). Post-edit verification now runs fast syntax checks plus the project's lint/typecheck checks for affected files; `--verify-full` runs every configured check, and `sk review --verify` adds a separate verification section with its own exit code (#423). Tests 1598 → 1761.
- `v0.31.0` — **review foundation.** Code intelligence lands as approval-free tools: `search_code`/`glob_files` (#398), line-range `read_file` (#399), and `repo_map` for deterministic trees, languages, and top-level symbols (#400). `sk review` and `/review` now share one evidence-backed engine: working-tree/staged/commit/base/PR targets and a structured diff model (#401), validated findings with file:line evidence (#402), the CLI/slash surface (#403), an advisory worth-it assessment (#408), and optional GitHub/Bitbucket PR comments (#410). Review is read-only and never edits the branch. 22 built-in tools. Tests 1504 → 1597.
- `v0.30.0` — **security release.** The egress allowlist is now enforced on every redirect hop, not just the initial URL: an allowlisted host could `302` anywhere and the agent fetched the target and returned it to the model, which is the exact threat #328 exists to stop (#371). The test that appeared to cover this passed before *and* after the fix, because it stubbed the guard rather than DNS and so asserted a DNS failure (#371). `PreToolUse` hooks now fail closed: a handler that raised was skipped, and an exception mid-iteration discarded denies already collected (#373). Checkpoints, the egress audit row and the FTS index report write failures instead of swallowing them, so `/rewind` can no longer report success over stale state (#373). Carries the still-unreleased `~/.sidekick` 0700/0600 fix (#301) and API-key masking (#269). Release integrity: `sk --version` now exists, the workflow can no longer strand a published version with no tag, and a burned version number fails CI (#372). MCP tool lists are cached per connection (#314). Credential storage, shell expansion and `python -m sk` are now actually tested, and coverage is a required check instead of a piped-through-`tail` no-op (#374). Docs match the shipped behaviour (#375). Tool names derive from the schema, so #368's residue detector recognises every tool instead of 17 of 19 (#376). Tests 1052 → 1389.
- `v0.29.0` — unparsed tool-call markup is no longer posted as an answer (#367/#368): the prose is kept, the markup is dropped, and the turn is bounded at 2 corrective retries before a plain error. Alongside it on main but **not yet published**: the `~/.sidekick` 0700/0600 fix (#301) and API-key masking (#269).
- `v0.28.0` — **burned.** Existed in `src/sk/__init__.py` for three minutes and was superseded before tagging, so the number can never be published. Listed in `BURNED_VERSIONS` in `tests/test_release_integrity.py`; a new gap now fails that test.
- `v0.27.0` — **security release.** Hostile-repo RCE chain closed: project files can no longer redirect traffic (`provider`/`model`) or self-approve shell (`[project].approved_commands`), and repo-supplied commands need `SIDEKICK_TRUST_REPO=1`. Irreversible shell commands are refused *before* approval (#298), so `--yes`/`/yolo`/`--allow-writes` no longer bypass the blocklist — **behaviour change**. Protected write paths (shell rc, credential dirs, `.git/hooks`, the agent's own config) are denied (#305). Credentials redacted from the audit trail, shell log and notifications (#300). MCP gets an environment allowlist, URL validation, a fail-closed trust tier and cross-origin header protection (#302, #303). One SSRF chokepoint for every outbound fetch, closing the CGNAT/cloud-metadata gap (#304). Compaction no longer silently no-ops and no longer discards its own retained tail (#306, #307). TUI prompt no longer sent twice; no more duplicate `tool_call_id`s (#309, #310). Fixed an order-dependent test failure (#317). Tests 828 → 981; layered on #264/#265/#266 rather than duplicating them.
- `v0.26.0` — security + robustness fix batch (#220–#229), QA regression suite (#231), retry breaker (#230), tester skill (#236), session-deny + edit-first (#237, #238), research delegate (#239), partial-output errors (#240), empty-toollist fallback, folding removal.
- `v0.25.0` — image input (#178), JS extraction (#182), profiles (#189), TUI forgiveness batch (#161, #163–#168), dispatch write-block (#208), reasoning/control hygiene (#206).
- `v0.24.0` — agent reliability: adaptive budgets lean-local/frontier-on-API (#198), exhaustion synthesis, reasoning-effort control (#201), empty-args guard.
- `v0.23.0` — image generation (#192), persistent shell sessions (#179), session search (#190).
- `v0.22.0` — cross-provider picker (#139), theme pack (#138), README + website catch-up docs.
- `v0.21.0` — website plan/picker docs (#135), picker full-list fix (#136).
- `v0.20.0` — build/plan modes (#131), TUI model picker (#130).
- `v0.19.1` — website feature-docs follow-up.
- `v0.19.0` — agent-capability batch: read-only mode (#98), session slash parity (#100), checkpoints + /rewind (#103), project memory (#105), MCP client (#107).
- `v0.18.0` — support + adoption: doctor --fix/report (#70), release policy + guard (#71), contributor growth + good-first pipeline (#72).
- `v0.17.0` — transparency gaps: anthropic streaming (#62), thinking display (#63), stale-card rejection (#64; cards + live Markdown shipped earlier in b0ec8fb).
- `v0.16.0` — version-bump release (publishes the v0.14.0 skills set to PyPI after the publisher fix).
- `v0.15.0` — version-bump release carrying the skills compound (#48–50); first PyPI publish of that code (`v0.14.0` was tagged in code but never published — PyPI went 0.13.0 → 0.15.0).
- `v0.14.0` — skills compound: plugin design (#48), user-defined tools (#49), skill registry (#50).
- `v0.13.0` — groq provider fix (contributor branch).
- `v0.12.0` — daemon compounds: scheduled tasks (#38), background runs (#39), session allowlist (#40), teammate-pilot digest (#41).
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
