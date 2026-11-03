# Sidekick Developer Plan

**Status:** Draft for maintainer review — no implementation started
**Date:** 2026-11-03
**Baseline:** `abfa95d` on `main`
**Baseline release:** `0.30.0`, 1,467 tests, 80.01% coverage, 19 tools

> This is a proposal, not a replacement for `ROADMAP.md`. If accepted, the
> versioned roadmap sections should be updated by PR, and this document should
> either move under `docs/` permanently or be folded into `ROADMAP.md`.

---

## 0. Executive summary

Turn Sidekick from a broad local-first assistant into a **local-first developer
agent with one defensible flagship: evidence-based code review and an editable
review harness**.

The plan is deliberately narrow:

1. **See the code** — add code search, repo maps, and useful file reading.
2. **Judge the code** — build a real review engine that produces evidence-backed
   findings.
3. **Prove the code** — run project checks and feed real diagnostics back into
   the agent.
4. **Let teams shape it** — make the review harness layered, editable,
   inspectable, and testable.

The review wedge ships before sandboxing, IDE integration, team SaaS, or
subagents because review is **read-only** and can be made safe today. It also
fits Sidekick's existing strengths: local-first execution, approval gates,
auditability, and small-model-friendly design.

**Success looks like this:**

- A developer runs `sk review --base main` on a real repository and gets a
  concise review with file:line evidence, severity, and a concrete fix.
- A team commits `.sidekick/review.toml` and can see exactly which rules ran,
  which were ignored, and why.
- A maintainer changes a rule and immediately sees its effect on a seeded-bug
  corpus.
- It works fully offline with a local model; cloud models are optional.
- The output is trustworthy enough to use in CI.

**Not in this plan:** general coding-agent parity, autonomous commits/pushes, an
IDE extension in v1, team SaaS, subagents, or Windows support. Those are later
bets.

---

## 1. Starting point

### What is already strong

- **Local-first runtime:** Ollama default, BYOK cloud providers, six runtime
  dependencies, no LangChain or vector database.
- **Policy spine:** approval gates, hard refusals, deny-by-default egress,
  redirect-hop enforcement, audit ledger, project trust boundaries.
- **Deterministic grounding:** real sysinfo, local facts, and web facts injected
  before the model; outside content fenced as untrusted data.
- **Small-model discipline:** text-JSON tool fallback, capability profiles,
  token budgeting, compaction, failure circuit breakers.
- **Test culture:** 1,467 tests, Hypothesis property tests, security regression
  tables, coverage floor, CI watchdog, trusted PyPI publishing.
- **Surfaces:** CLI, TUI, voice, MCP server and client, daemon/jobs, skills,
  plugins, checkpoints, profiles.

### What is missing for developers

| Gap | Evidence |
|---|---|
| No approval-free code search | `exec` has no `grep`/`rg`; the agent must run `shell rg` and interrupt the user |
| Weak file reading | `read_file` returns the first 8KB with no line range or offset |
| No repo map or symbols | No code-aware navigation or outline |
| No test/lint/typecheck loop | Post-edit verification only compiles edited Python files |
| Review is a prompt wrapper | `/review` = `git diff HEAD`, 12k-char cap, no targets, chunking, or structured output |
| Default step budget is too low | Local and unknown models get 5 steps; named frontier profiles get 15; the Harbor adapter overrides to 40 |
| No provider abstraction | Anthropic backend reimplements the agent loop and imports private symbols (#312) |
| Security ceiling | No OS sandbox (#329); `shell curl` bypasses egress; hooks use `shell=True` (#268) |
| Maintainability debt | `agent.py` is 3,095 lines; TUI 1,880; broad excepts and lenient type gates (#270) |
| Docs drift | Website says 1,399 tests; actual is 1,467. Changelog stops at 0.29.0. Portfolio says 1,212 tests/0.29.0 |

### Constraints to respect

- Keep the local-first, zero-heavy-dependency bias.
- Keep approval gates and auditability; do not make review silently write or
  execute.
- Keep small-model behavior viable; deterministic checks must carry some of the
  review burden.
- Do not expand into five new surfaces; make one workflow excellent.

---

## 2. Product thesis and positioning

### One-liner

> **The local-first developer agent for code you cannot send to the cloud — with
> review rules you can inspect and edit.**

### Target user

**Primary:** an individual developer or consultant working in a privacy-sensitive
environment, using a local model or a BYOK cloud model.

**Secondary:** a small team that wants code review without sending source to a
third-party service, and wants rules checked into the repo.

**Not the initial target:** enterprises needing SSO, RBAC, and audit exports.
That is a later phase.

### Why code review is the wedge

- It is **read-only**, so it can ship before sandboxing.
- It is **recurring**, not a one-off demo.
- It is **privacy-sensitive**, which is exactly where local-first wins.
- It is **team-specific**, which makes an editable harness valuable.
- It produces a clear artifact — findings — that can be measured.
- It demonstrates the policy spine instead of merely claiming it.

### Product principles

1. **Evidence over opinion.** No finding without a file, line, quoted evidence,
   and a failure mode.
2. **Local by default.** Cloud is optional, not required.
3. **Rules are code.** Versioned, reviewable, testable, and attributable.
4. **No silent skips.** Report files reviewed, files skipped, changed-line
   coverage, and budget used.
5. **Safe by default.** Project-supplied rules are untrusted; they cannot
   execute code or disable protected security rules.
6. **One command, zero config.** `sk review` must be useful immediately; depth
   is optional.
7. **Small-model-friendly.** Deterministic checks and tight context do the
   heavy lifting; the model adds judgment.
8. **Measure quality.** A rule change is not "better" until the corpus says so.

---

## 3. The product: three loops

```text
target diff
    |
    v
[1] Code intelligence  --->  changed code + surrounding context + repo map
    |
    v
[2] Review engine      --->  findings + evidence + severity
    |          ^
    |          |
    |     editable harness
    |
    v
[3] Verification loop  --->  tests / lint / typecheck / diagnostics
    |
    v
Markdown / JSON / SARIF / CI exit code
```

- **See:** code search, line-range reads, repo map.
- **Judge:** review engine plus layered harness.
- **Prove:** project checks and real diagnostics.

The harness is the moat. The engine is the product. Code intelligence and
verification are what make the engine credible.

---

## 4. Workstreams

### 4.1 Review engine

**Outcome:** `sk review` and `/review` produce a useful, evidence-backed review
of a real diff.

#### Targets

| Target | Command | Notes |
|---|---|---|
| Working tree + staged | `sk review` | Default; matches current `/review` behavior but improves it |
| Staged only | `sk review --staged` | Pre-commit use |
| Branch vs base | `sk review --base main` | Merge-base diff; the developer default for PR prep |
| Specific commit | `sk review --commit <sha>` | Review a single commit |
| Pull request | `sk review --pr 123` | Uses `gh` when available; clean error otherwise |
| Path filter | `sk review --path src/` | Limit scope |

#### Pipeline

1. **Resolve target** — repo root, base/head refs, merge-base, commit metadata.
2. **Collect diff** — file status, renames, binary detection, mode changes,
   generated/lockfile filtering.
3. **Budget and chunk** — estimate tokens per file; prioritize source files;
   chunk large diffs by hunk; cap total diff size.
4. **Assemble context** — changed hunks, surrounding lines, related tests,
   project rules, and a small repo map.
5. **Run deterministic checks** — Python AST checks, optional `ruff`/`bandit`,
   project-provided checks. These findings are labeled `source=check`.
6. **Run model review** — per-file or per-chunk structured review; map-reduce for
   large diffs; 2–4 concurrent requests.
7. **Validate findings** — file exists, line is in the diff or provided context,
   evidence quote exists, severity is valid, no duplicates.
8. **Aggregate** — global summary, dedupe, sort by severity and file.
9. **Report coverage** — files reviewed, files skipped and why, changed-line
   coverage, token/latency budget.

#### Finding schema

```json
{
  "id": "finding-001",
  "severity": "blocker",
  "category": "correctness",
  "file": "src/api/auth.py",
  "line": 142,
  "end_line": 147,
  "evidence": "if user.is_admin or token.role == 'admin':",
  "problem": "The role check trusts a client-controlled token claim.",
  "impact": "Any user who can mint a token with role=admin bypasses authorization.",
  "suggestion": "Validate the role against the server-side user record, not the token payload.",
  "confidence": "high",
  "rule_id": "security/trust-client-claims",
  "source": "model"
}
```

**Hard invariants:**

- No finding without valid file, line, and evidence.
- Invalid findings are dropped and counted, never silently shown.
- Severity taxonomy: `blocker`, `major`, `minor`, `nit`.
- Categories: `correctness`, `security`, `performance`, `maintainability`,
  `tests`, `style`.
- Every finding carries rule provenance and model/check source.

#### Output

- **Markdown** default for humans.
- **JSON** for automation.
- **SARIF** for CI/code scanning integration.
- Summary includes counts, files skipped, coverage, model, harness hash, latency,
  and token cost.
- `--fail-on blocker|major|never` controls exit code for CI.
- `--explain` shows the resolved harness and rule provenance; `--check-config`
  validates it.

#### Safety and performance

- Read-only by default: no writes, no commands, no shell.
- Project content is fenced as untrusted input.
- No network beyond the configured model provider.
- Cache per-file results by content hash to avoid re-reviewing unchanged files.
- Hard caps: max files, max diff bytes, max findings, max runtime; all reported
  when hit.
- Local 7B target: 500-line diff in under 60 seconds; frontier target: under
  20 seconds.

#### Suggested module layout

```text
src/sk/review/
  __init__.py
  engine.py       # orchestration
  diff.py         # target resolution + diff collection
  context.py      # context assembly + budgeting
  schema.py       # findings, validation, serialization
  harness.py      # layered config load/merge/explain
  output.py       # Markdown / JSON / SARIF
  rules/          # built-in deterministic checks
```

Reuse existing code where possible: `gitdiff.py`, `trust.py`, `tools/read.py`,
`model_profiles.py`, and the `test_eval.py` pattern.

#### Acceptance criteria

- Seeded corpus: **≥60% recall** on blocker/major bugs and **≤20% false
  positives** on clean diffs for the first release; targets rise to 75%/15% by
  v1.
- **100% evidence validity**: every reported finding points to a real file/line
  and the quoted text exists.
- **≥90% changed-line coverage**, or an explicit report of what was skipped and
  why.
- `--json` schema is stable and covered by tests.
- `--sarif` output validates against the SARIF schema.
- `sk review` and `/review` share one engine; no duplicated review logic.

---

### 4.2 Editable review harness

**Outcome:** a team can change how Sidekick reviews code without forking the
agent.

#### Definition

The harness is the **review policy pack**, not the agent loop. It contains:

- rules and guidance
- severity policy
- file include/ignore patterns
- context and diff budgets
- model profile
- output contract
- optional deterministic checks
- rule fixtures for evaluation

It cannot execute arbitrary code, define shell commands, or silently disable
security checks.

#### Layers and precedence

```text
built-in defaults                  (versioned with Sidekick)
    < user global                  (~/.sidekick/review.toml)
    < project                      (<repo>/.sidekick/review.toml)
    < CLI overrides                (--rule, --severity, --disable)
```

**Merge semantics:**

- Scalars: highest layer wins.
- Lists: append unique values.
- Rules by ID: higher layer overrides fields, not the entire rule.
- `disable = true` removes a rule, except protected security rules.
- Protected built-in security rules cannot be disabled by a project layer; only
  the user global layer can disable them, with an explicit flag.
- Project rules are **untrusted by default**. A cloned repo must not be able to
  inject instructions into a review just because the user ran `sk review`.
  Project harness loading requires trust, or an explicit
  `--trust-project-rules`.
- All project-supplied prose is fenced as data.

#### Example

```toml
# .sidekick/review.toml
schema = 1

[review]
model = "smart"
max_findings = 25
fail_on = "major"
context_lines = 8
include = ["**/*.py", "**/*.ts", "**/*.tsx"]
ignore = ["**/generated/**", "**/*.lock", "**/vendor/**"]

[[rules]]
id = "security/no-dynamic-exec"
severity = "blocker"
applies_to = ["**/*.py"]
guidance = """
Flag eval(), exec(), and subprocess calls with shell=True when the
input is not a literal or is reachable from request data.
"""
```

#### Explain and validate

- `sk review --explain` prints:
  - which layers loaded
  - which were ignored and why
  - effective rules and severities
  - resolved model, budgets, include/ignore patterns
  - harness hash for reproducibility
- `sk review --check-config` validates:
  - schema version
  - unknown keys
  - duplicate rule IDs
  - invalid globs
  - invalid severity values
  - rules with no fixtures (warning, not failure)
- Findings include `rule_id` and `source_layer`, so a reviewer knows whether a
  rule came from built-in, user, or project config.

#### Rule testing

Each rule has positive and negative fixtures:

```text
tests/review/rules/security/no-dynamic-exec/
  positive.py
  negative.py
  expected.json
```

A rule change without a fixture is allowed but flagged; a protected security
rule requires one.

#### Acceptance criteria

- Merge/precedence tests cover every layer and conflict case.
- Project layer cannot disable protected security rules.
- `--explain` output is deterministic and includes the harness hash.
- Invalid config produces an actionable error, not a traceback.
- A project rule cannot execute code, spawn a process, or make a network call.
- At least 10 built-in rules have positive/negative fixtures.

---

### 4.3 Code intelligence

**Outcome:** the agent can find and read code without interrupting the user for
every search.

#### New or improved tools

1. **`search_code(pattern, path?, glob?, max_results?)`**
   - Uses `rg` when available; falls back to `grep` or a Python walker.
   - Respects `.gitignore`, binary detection, symlink rules, and read-path
     guards.
   - Returns `file:line` plus a short snippet.
   - Read-only, no approval, audit-logged.

2. **`glob_files(pattern, path?)`**
   - Returns matching paths, capped and sorted.
   - Respects the same guards.

3. **`read_file(path, start_line?, end_line?, max_chars?)`**
   - Line-range reads with line numbers.
   - Exact slice, not "first 8KB."
   - Handles UTF-8 replacement and large-file refusal.

4. **`repo_map(path?, max_tokens?)`**
   - Directory tree, languages, file sizes.
   - Python symbols via `ast`; other languages get top-level definitions via
     regex.
   - Capped at a small token budget.

5. **`git_diff(target?)`** — optional; could remain part of review/diff tooling
   rather than a general tool.

#### Acceptance criteria

- `search_code` p95 under 300ms on a 10k-file repo with `rg`.
- Search never requires approval and never writes.
- `read_file` returns exactly the requested lines, including line numbers.
- Path traversal and protected-path tests pass.
- Results are capped and truncation is explicit.

---

### 4.4 Verification loop

**Outcome:** Sidekick can run the project's real checks and use the results,
instead of only compiling Python files.

#### Configuration

Project checks live in a trusted file, for example:

```toml
# .sidekick/checks.toml
[checks]
lint = "ruff check ."
typecheck = "mypy src"
test = "pytest -q"
```

Equivalently, a `SIDEKICK.md` section can define the commands. The file is
trust-gated like other project commands.

#### Behavior

- `sk check` runs all configured checks; `sk check --changed` runs checks
  relevant to changed files where the tool supports it.
- Each command goes through the normal approval gate and audit log.
- Output is captured, truncated safely, and parsed into structured diagnostics.
- Parsers for common tools first: `pytest`, `ruff`, `mypy`, `eslint`, `tsc`,
  `go test`; fallback to raw output with a clear "unparsed" marker.
- On failure, diagnostics are fed back to the model as structured context, not a
  giant blob.
- A timeout or spawn failure is reported as `unknown`, never `pass`.
- `sk review --verify` can include check findings in the review output.

#### Post-edit verification

Replace the current "compile edited Python files" behavior with:

1. fast syntax check for edited files
2. configured lint/typecheck for affected files, if available
3. optional full `sk check` when the user asks or a project config enables it

#### Acceptance criteria

- Failing `pytest`, `ruff`, and `mypy` runs are detected and parsed.
- A timeout or missing command cannot be reported as success.
- The model receives a compact, structured diagnostic summary.
- No check runs without approval.
- Verification output is available in JSON for automation.

---

### 4.5 Agent runtime and provider fixes

**Outcome:** the agent has enough budget and a single loop across providers.

#### Adaptive step budgets

- Coding and review tasks get more steps than chat.
- Local models: 10–15 steps by default.
- Frontier models: 30–40 steps for coding/review.
- Unknown models: 15 steps, not 5.
- `--max-steps` is a first-class CLI option.
- The final answer reports why the loop stopped: completed, budget exhausted,
  tool failure, or model error.

#### Provider abstraction

- Define a provider protocol for chat, streaming, tool calls, capabilities,
  context window, and cost.
- Implement OpenAI-compatible and Anthropic providers behind it.
- `run_agent` uses one loop.
- Remove private symbol imports from `anthropic_backend.py`.
- Add parity tests that run the same scenarios against both providers.
- Do this before advertising "review works everywhere."

#### Git workflow tools

- `git_status`, `git_diff`, `git_branch`, `git_commit`, `git_push`, and a PR
  helper.
- Reads run automatically; writes and pushes stay approval-gated.
- This removes the current need to use prompted `shell` for routine git work.

#### Acceptance criteria

- Coding/review tasks get at least 20 steps on local and 30 on frontier by
  default.
- Provider parity suite passes on both backends.
- No private imports from `agent.py` in provider code.
- Git read operations require no approval; git writes do.
- Stop reason is surfaced in the final output.

---

### 4.6 Platform hardening

**Outcome:** the security story remains credible as the developer workflow
grows.

- **Sandboxed shell (#329):** bubblewrap on Linux, Seatbelt on macOS; deny
  network by default; mount repo read-only and workspace writable; fall back to
  approval when unavailable.
- **Hooks without `shell=True` (#268):** argv-based execution by default; shell
  mode is explicit and documented.
- **Security CI (#271):** `pip-audit`, `bandit`, dependency review, secret
  scanning.
- **Coverage uplift:** CLI chat from 9% to ≥50%, daemon from 46% to ≥60%, router
  from 68% to ≥80%, overall ≥82%.
- **Split god files (#270):** at minimum extract prompt assembly, context
  budgeting, tool dispatch, and provider calls from `agent.py`; split the TUI
  screen and system CLI commands.
- **Tighten gates incrementally:** enable more Ruff rules and
  `disallow_untyped_defs` module by module.
- **Windows decision:** either support it or document POSIX-only clearly; do not
  leave it ambiguous.
- **Release hygiene:** per-release AUR/conda install smoke tests.

**Acceptance criteria:** sandbox tests deny network and out-of-workspace writes;
hooks work in argv mode; security scans are required checks; coverage gates
pass.

---

### 4.7 Evals and benchmarks

**Outcome:** review quality is measured, not asserted.

#### Review corpus

- 40–60 diffs across Python, TypeScript/JavaScript, and Go.
- Half contain seeded bugs; half are clean.
- Each bug has expected file, line range, category, severity, and rule.
- Include negative cases that should produce no findings.
- Store expected results in JSON; run offline in CI.

#### Metrics

| Metric | Target |
|---|---|
| Blocker/major recall | ≥60% first release, ≥75% v1 |
| False-positive rate on clean diffs | ≤20% first release, ≤15% v1 |
| Evidence validity | 100% |
| Invalid file/line rate | 0% |
| Changed-line coverage | ≥90% or explicitly reported |
| Local 7B latency | ≤60s for 500-line diff |
| Frontier latency | ≤20s for 500-line diff |
| Cost per review | tracked and reported |

#### Coding tasks

- 20–30 small repository tasks with tests: fix a failing test, add an endpoint,
  refactor a function, update a dependency.
- Measure pass@1, steps used, latency, and token cost.
- Run nightly with local and cloud models; run offline fixture evals on every PR.

#### Harness evals

- Every built-in rule has positive/negative fixtures.
- A rule change runs only that rule's fixtures in PR CI and the full corpus
  nightly.
- A regression in recall or precision blocks the release.

#### Acceptance criteria

- Offline review corpus runs in CI in under five minutes.
- Nightly live eval produces a scoreboard with pass@1/latency/cost.
- A release candidate cannot ship with a corpus regression beyond the agreed
  threshold.

---

### 4.8 Adoption, docs, and distribution

**Outcome:** the project is discoverable and the public story matches the
product.

- **Reposition the README and website:** lead with "review, test, and edit code
  without your source leaving the machine," not "19 tools."
- **Demo:** a 30–60 second terminal recording of `sk review --base main` on a
  real repo.
- **Benchmarks:** publish the review corpus results and the local-vs-cloud
  comparison.
- **Docs:** a dedicated `docs/review.md` and `docs/review-harness.md` with
  examples.
- **Fix stale claims:** website test count (1,399 vs 1,467), changelog (stops at
  0.29.0), portfolio card (1,212 tests, 0.29.0, "8k installs").
- **Add `SECURITY.md`:** scope, supported versions, disclosure process, threat
  model link.
- **Add issue/PR templates:** bug report, feature request, review-rule proposal.
- **Add GitHub topics and a real description:** local-first, code-review, ollama,
  mcp, developer-tools.
- **Design partners:** recruit 3–5 developers with privacy-sensitive repos;
  observe real review runs; do not add telemetry in v1.

**Acceptance criteria:** docs match the shipped feature set; website/portfolio
claims are current; 3 design partners have run `sk review` on real repos.

---

## 5. Phasing and sequencing

Estimates assume 1–2 maintainers. Calendar time will stretch if part-time.

| Phase | Duration | Deliverables | Exit criteria |
|---|---|---|---|
| **0. Decisions and baseline** | 2–3 days | Lock positioning, scope, harness format, review schema; add corpus skeleton | Decisions signed off; no open blockers |
| **1. Review foundation** | ~2 weeks | Target resolver, diff pipeline, code search/read ranges, `sk review` MVP, Markdown/JSON output | Review works on a real repo; evidence validation passes; code search shipped |
| **2. Harness and verification** | ~2–3 weeks | Layered harness, `--explain`/`--check-config`, built-in rules, seeded corpus, project checks, `sk check` | Harness precedence tests pass; review corpus thresholds met; verification detects failures |
| **3. Runtime and platform** | ~2–3 weeks | Adaptive budgets, provider abstraction, git tools, sandbox/hooks/security CI, coverage uplift | Provider parity, sandbox tests, security scans green |
| **4. Evals and adoption** | ongoing | Nightly live eval, scoreboard, docs/demo, design partners, team/air-gapped groundwork | Public benchmark; 3 design partners; docs current |

**Suggested version mapping (tentative):**

- `v0.31.0` — review foundation: code intelligence + `sk review` MVP.
- `v0.32.0` — editable harness + verification loop.
- `v0.33.0` — provider abstraction + platform hardening.
- `v0.34.0` — evals, benchmark, adoption.

Do not promise these versions until Phase 0 is complete.

---

## 6. Release gates

A release is not ready unless:

- Review corpus thresholds are met or explicitly waived with a documented reason.
- 100% of findings have valid evidence.
- Harness precedence and protected-rule tests pass.
- Code search and line-range read tests pass.
- Verification cannot report a false pass.
- `ruff`, `mypy`, and the full test suite pass.
- Coverage is at or above the current gate, with no regression in the targeted
  modules.
- Docs and changelog match the release.
- No known blocker in the review path.

---

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Review produces noisy, untrustworthy findings | Evidence validation, deterministic checks, conservative severities, seeded corpus, precision thresholds |
| Project rules inject prompts or weaken security | Untrusted by default, fencing, no execution, protected security rules, explicit trust flag |
| Large diffs exceed context or cost | Per-file chunking, content-hash caching, max files/findings, coverage report |
| Local 7B quality is too low | Hybrid deterministic + model review; default to smart model; publish honest benchmarks |
| Scope expands into another general coding agent | Non-goals, one flagship, defer IDE/team/subagents |
| Provider divergence makes review inconsistent | Provider abstraction before multi-provider marketing; parity tests |
| Sandbox work delays the wedge | Ship read-only review first; sandbox is a prerequisite for autonomous verification, not review |
| Tests create false confidence | Live eval harness and corpus; offline tests only prove regression safety |
| Docs drift again | Gate version and test count, or remove hardcoded counts; docs review in release checklist |
| Adoption remains low | Repositioning, demo, benchmark, topics, design partners; measure feedback, not stars alone |

---

## 8. Decisions needed before implementation

1. **Is code review the flagship?**
   Recommendation: **yes**. It is the best fit for local-first, privacy, and
   editable rules.

2. **Who is the first user?**
   Recommendation: **privacy-sensitive individual developers first; teams
   second.**

3. **Harness format?**
   Recommendation: **TOML config plus optional Markdown rule files**; no
   arbitrary code execution.

4. **Project rule trust?**
   Recommendation: **untrusted by default; protected security rules cannot be
   disabled by a project layer.**

5. **Default review target?**
   Recommendation: **`git diff HEAD` (working + staged)**; `--base main` for
   branch/PR prep.

6. **CI behavior?**
   Recommendation: **Markdown default, JSON/SARIF optional; `--fail-on major`
   is opt-in.**

7. **Deterministic checks in v1?**
   Recommendation: **yes for Python AST/ruff-style checks; project checks come
   in Phase 2.**

8. **Windows?**
   Recommendation: **defer; document POSIX-only until the developer wedge is
   proven.**

9. **Telemetry?**
   Recommendation: **no telemetry in v1; use design partners and opt-in
   feedback.**

---

## 9. First backlog

1. Add `search_code` and `glob_files` tools with path-guard and fallback tests.
2. Add line-range support to `read_file`.
3. Add `repo_map` v1: tree, languages, Python symbols, token cap.
4. Build review target resolver and diff model.
5. Add finding schema, validation, and Markdown/JSON serialization.
6. Wire `sk review` and `/review` to the same engine.
7. Add harness loader, merge logic, `--explain`, and `--check-config`.
8. Add built-in review rules and the first 10 seeded cases.
9. Add project checks config, command runner, and diagnostic parsers.
10. Add adaptive `max_steps` and `--max-steps`.
11. Add provider protocol and Anthropic parity tests.
12. Update README/website/portfolio; add `SECURITY.md` and issue templates.

---

## 10. Definition of done

This plan is successful when:

- `sk review --base main` is useful on a real repository without custom
  configuration.
- The review is evidence-backed, reports coverage, and does not silently skip
  files.
- A team can edit `.sidekick/review.toml`, run `--explain`, and see exactly what
  changed.
- Rule changes are measured against a seeded corpus.
- `sk check` runs real project checks and feeds diagnostics back to the agent.
- Code search and line-range reads remove the need for approval-heavy shell
  searches.
- The agent has enough steps for developer work and a single provider loop.
- The public story is "local-first code review with editable rules," not "an
  assistant with 19 tools."
