# Sidekick Developer Plan

**Status:** Phase 1 shipped in `v0.31.0`; Phase 2 (editable review pack + verification) shipped in `v0.32.0`
**Date:** 2026-11-03
**Accepted:** 2026-11-04
**Revised:** 2026-11-11 — Phase 2 shipped; editable review packs, `sk check`, and post-edit verification are on `main`
**Prior revision:** 2026-11-08 — Phase 1 shipped; code intelligence and the evidence-backed review engine are on `main`
**Baseline:** `a46127e` on `main` (current at revision; audit baseline
`abfa95d`, accepted at `780c8bd`)
**Baseline release:** `0.30.0`, 1,504 tests, 21 tools
**Tracking:** [v0.31.0 milestone](https://github.com/Faisal-Fayaz/sidekick/milestone/6)
and [v0.32.0 milestone](https://github.com/Faisal-Fayaz/sidekick/milestone/7)
(both closed). Phase 2 items are listed in §9.

> `ROADMAP.md` remains the source of truth for versioned scope. This document
> holds the detailed design and accepted defaults behind the developer
> workstream. The first six review-foundation items were tracked in the v0.31.0
> milestone; items 8–10 shipped in v0.32.0.

> **Revision note (2026-11-07).** The first accepted draft framed review as a
> product pivot ("turn Sidekick into a developer agent", "the public story is
> review, not an assistant with 19 tools"). That framing is withdrawn.
> Sidekick stays a broad local-first terminal agent; review is the first
> developer feature in a growing developer workstream. The engineering detail
> below still applies, with the scope and identity corrections marked inline.

---

## 0. Executive summary

Add a **developer workstream** to Sidekick, beginning with **evidence-based code
review** as its first flagship capability. Sidekick remains a broad local-first
terminal agent: chat, code intelligence, memory, web, voice, skills, MCP, and
the approval/audit spine stay first-class. Review is a new feature, not a new
identity, and it must not narrow the harness to review.

The first developer phase is deliberately narrow **in scope, not in product**:

1. **See the code** — add code search, repo maps, and useful file reading.
2. **Judge the code** — build a real review engine that produces evidence-backed
   findings, plus an advisory worth-it assessment of the change itself.
3. **Prove the code** — run project checks and feed real diagnostics back into
   the agent.
4. **Let teams shape it** — make the review harness layered, editable,
   inspectable, and testable.

The review workstream ships before sandboxing, IDE integration, team SaaS, or
subagents because review is **read-only** and can be made safe today. It also
extends Sidekick's existing strengths: local-first execution, approval gates,
auditability, and small-model-friendly design.

**Review is the first rung, not the whole ladder.** Later developer
capabilities — verification, guided fixes, refactors, PR prep, codebase
explanation — reuse the same policy packs, gates, and eval culture. The
sequencing rule is one workflow excellent at a time; the product rule is that
the assistant stays whole while each rung is added.

**Success looks like this:**

- A developer runs `sk review --base main` on a real repository and gets a
  concise review with file:line evidence, severity, and a concrete fix.
- A branch review gets an honest, advisory answer to "is this change worth
  making, and is this the smallest version of it?" — backed by the diff and the
  stated intent, not by vibes.
- A team commits `.sidekick/review.toml` and can see exactly which rules ran,
  which were ignored, and why.
- A maintainer changes a rule and immediately sees its effect on a seeded-bug
  corpus.
- It works fully offline with a local model; cloud models are optional.
- The output is trustworthy enough to use in CI.
- A user who never runs `sk review` still gets the same broad assistant; no
  capability is removed or demoted to make room for review.

**Not in this phase:** general coding-agent parity, autonomous commits/pushes,
an IDE extension in v1, team SaaS, subagents, or Windows support. These are
later bets, not permanent exclusions; the developer ladder in §5.1 names the
next candidates.

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
- **Test culture:** 1,597 tests, Hypothesis property tests, security regression
  tables, coverage floor, CI watchdog, trusted PyPI publishing.
- **Surfaces:** CLI, TUI, voice, MCP server and client, daemon/jobs, skills,
  plugins, checkpoints, profiles.

### What is missing for developers

| Gap | Evidence |
|---|---|
| Review context is v1 | Targets, diff model, repo map, code search, and line-range reads shipped (#398–#401); no semantic retrieval or editable context policy yet |
| No test/lint/typecheck loop | Closed: post-edit verification runs syntax checks plus the project's configured lint/typecheck checks, on every provider path (#280, #423, #433) |
| Review pack is built-in only | Rules, budgets, include/ignore, and worth-it policy are not project-editable yet; `--explain`/`--check-config` are Phase 2 |
| Default step budget is too low | Local and unknown models get 5 steps; named frontier profiles get 15; the Harbor adapter overrides to 40 |
| Provider abstraction | One loop drives both providers through a backend seam (`sk.backends`); the Anthropic path no longer keeps its own copy of the loop (#312, #431) |
| Security ceiling | The declarative sandbox landed in `v0.33.0` (#329): `~/.sidekick/sandbox.toml` denies reads outside the roots and network access from the shell. What a program does at runtime is still beyond a policy that reads command text, so OS-level isolation (bubblewrap / Seatbelt) is the remaining work; hooks still use `shell=True` (#268) |
| Maintainability debt | `agent.py` is 2,851 lines; TUI 1,880; broad excepts and lenient type gates (#270) |
| Docs freshness is manual | Version, roadmap, hero count, and AUR pins are release-checklist items; prose still needs review each release |

### Constraints to respect

- Keep the local-first, zero-heavy-dependency bias.
- Keep approval gates and auditability; do not make review silently write or
  execute.
- Keep small-model behavior viable; deterministic checks must carry some of the
  review burden.
- Ship one workflow excellently at a time, but do not remove or demote the
  assistant surfaces to make room. Developer capabilities are added as packs on
  the shared harness, not as a replacement for it.

---

## 2. Product thesis and positioning

### One-liner

> **The local-first terminal agent for developers — chat, code, and
> evidence-backed review on your hardware, with review rules you can inspect
> and edit.**

### Target user

**Primary:** an existing Sidekick developer who wants review in the same
local-first surface as chat, code reading/editing, shell, and memory.

**Secondary:** a privacy-sensitive individual or small team that starts with
review (no source sent to a third-party service, rules checked into the repo)
and stays for the assistant.

**Not the initial target:** enterprises needing SSO, RBAC, and audit exports.
That is a later phase.

### Why code review is the wedge

- It is **read-only**, so it can ship before sandboxing.
- It is **recurring**, not a one-off demo.
- It is **privacy-sensitive**, which is exactly where local-first wins.
- It is **team-specific**, which makes an editable review pack valuable.
- It produces a clear artifact — findings — that can be measured.
- It demonstrates the policy spine instead of merely claiming it.
- It **extends** the existing assistant instead of replacing it; the same repo,
  model, approval gates, audit trail, and memory serve every workflow.

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
9. **Additive, not reductive.** New developer features must not remove, demote,
   or reframe the assistant. They share the same harness and the same policy
   spine.
10. **Advisory on judgment.** The worth-it layer may challenge, inform, and
    recommend. It never blocks a human who has decided, and only concrete
    evidence-backed findings may gate CI.

---

## 3. The review workstream: four loops

```text
target diff + intent
    |
    v
[0] Worth-it check     --->  stated problem, alternatives, cost, case against
    |                         (advisory; never gates)
    v
[1] Code intelligence  --->  changed code + surrounding context + repo map
    |
    v
[2] Review engine      --->  findings + evidence + severity
    |          ^
    |          |
    |     editable review pack
    |
    v
[3] Verification loop  --->  tests / lint / typecheck / diagnostics
    |
    v
Markdown / JSON / SARIF / CI exit code
```

- **Challenge:** is the change itself worth making, and is this the smallest
  version of it? The review-side counterpart of the agent's CHALLENGE doctrine.
- **See:** code search, line-range reads, repo map.
- **Judge:** review engine plus layered review pack.
- **Prove:** project checks and real diagnostics.

The review pack is the moat for review; the engine is the product of this
workstream. Code intelligence and verification are what make the engine
credible. None of them define the whole Sidekick harness: the assistant loop,
doctrines, approvals, and audit remain shared core.

---

## 4. Workstreams

### 4.1 Review engine

**Outcome:** `sk review` and `/review` produce a useful, evidence-backed review
of a real diff.

**Status (2026-11-08):** shipped in `v0.31.0` (PR #411): all targets, structured
diff collection, evidence validation, Markdown/JSON/SARIF output, the advisory
worth-it assessment, and GitHub/Bitbucket PR comments. The editable review pack
and verification loop shipped in `v0.32.0` (#420–#423).

#### Targets

| Target | Command | Notes |
|---|---|---|
| Working tree + staged | `sk review` | Default; matches current `/review` behavior but improves it |
| Staged only | `sk review --staged` | Pre-commit use |
| Branch vs base | `sk review --base main` | Merge-base diff; the developer default for PR prep |
| Specific commit | `sk review --commit <sha>` | Review a single commit |
| Pull request | `sk review --pr 123` | GitHub via `gh` or token, Bitbucket Cloud/Server via API; clean error otherwise |
| Path filter | `sk review --path src/` | Limit scope |

#### Review modes

| Mode | Flag | Default | Gates CI? |
|---|---|---|---|
| Code review | (default) | always | yes, via `--fail-on` |
| Change assessment (worth-it) | `--assess` / `--no-assess` | on for `--base` and `--pr`; off for working-tree and staged reviews | no — always advisory |

Working-tree review stays fast. Branch/PR review gets the extra intent check
because that is where "should this exist?" is actionable. `--assess` forces it
on; `--no-assess` turns it off.

#### Pipeline

0. **Resolve intent** — PR title/body, commit messages, branch name, and an
   optional `--intent "..."`. If intent is absent, ask one round in interactive
   runs; in CI, record explicit assumptions. Never invent a problem statement.
1. **Resolve target** — repo root, base/head refs, merge-base, commit metadata.
2. **Collect diff** — file status, renames, binary detection, mode changes,
   generated/lockfile filtering.
3. **Budget and chunk** — estimate tokens per file; prioritize source files;
   chunk large diffs by hunk; cap total diff size.
4. **Assemble context** — changed hunks, surrounding lines, related tests,
   project rules, intent artifact, and a small repo map.
5. **Run deterministic checks** — Python AST checks, optional `ruff`/`bandit`,
   project-provided checks. These findings are labeled `source=check`.
6. **Run model review** — per-file or per-chunk structured review; map-reduce for
   large diffs; 2–4 concurrent requests.
7. **Assess the change** — when enabled, produce the advisory worth-it
   assessment from intent + deterministic signals + diff summary. One per
   review, not per file.
8. **Validate findings** — file exists, line is in the diff or provided context,
   evidence quote exists, severity is valid, no duplicates.
9. **Aggregate** — global summary, dedupe, sort by severity and file; attach the
   assessment separately from findings.
10. **Report coverage** — files reviewed, files skipped and why, changed-line
    coverage, token/latency budget, assessment on/off and intent source.

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

#### Change assessment (the worth-it layer)

The finding schema reviews *the code*. The assessment reviews *the decision*:
is this change worth making at all, and is this the smallest change that solves
the stated problem? It is the review-side counterpart of the agent's CHALLENGE
doctrine (#341): challenge before work, verify the premise after work.

It answers, with evidence:

- What problem is this change claiming to solve, and where is that stated?
- Is the change the smallest viable fix, or does it add machinery the problem
  does not justify?
- What alternatives were available (reuse, config, dependency, deletion)?
- What does it cost to keep (dependencies, API surface, migration, tests)?
- What would make the change *not* worth it?

```json
{
  "assessment": {
    "stated_intent": "Replace the ad-hoc retry loop with a queue because retries lose jobs on restart.",
    "intent_source": "pr-body",
    "signals": [
      "auth/ is touched",
      "new runtime dependency: rq",
      "no test changes for the restart path",
      "+412/-38 lines across 9 files"
    ],
    "questions": [
      {
        "question": "Is a queue required for one restart-safe job?",
        "evidence": "The diff adds a broker, worker, and deployment config for a single hourly job.",
        "alternative": "Persist the pending job to SQLite and re-run it at startup; no new service."
      }
    ],
    "risks": [
      "The queue adds an always-on worker the current deployment does not run."
    ],
    "verdict": "proceed-with-changes",
    "confidence": "medium",
    "assumptions": [
      "Intent inferred from PR body; no design doc was linked."
    ],
    "advisory": true
  }
}
```

**Hard invariants:**

- `advisory` is always `true`. The assessment never contributes to `--fail-on`,
  exit codes, or SARIF.
- Every claim cites a diff line, metadata field, or tool result. No fabricated
  intent and no invented alternatives.
- If no intent is available, emit `intent_source: "inferred"` plus explicit
  assumptions; in interactive runs ask one round (max) before reviewing.
- One assessment per review, not per file.
- Deterministic signals (diff size, new dependencies, missing test changes,
  migration/rollback, public API, security-sensitive paths) are computed
  without the model; the model adds judgment only on top of them.
- When the change is straightforward, the assessment is one line: "no material
  worth-it concern; proceeding to line review." No ceremony, no interrogation.

#### Output

- **Markdown** default for humans.
- **JSON** for automation.
- **SARIF** for CI/code scanning integration (findings only; the assessment is
  not a SARIF result).
- Summary includes counts, files skipped, coverage, model, review-pack hash, latency,
  and token cost.
- The **change assessment** appears before the findings in Markdown and as an
  `assessment` object in JSON; it is visibly labelled advisory.
- `--fail-on blocker|major|never` controls exit code for CI and ignores the
  assessment.
- `--explain` shows the resolved review pack, rule provenance, whether assessment
  ran, and the intent source; `--check-config` validates it.

#### Safety and performance

- Read-only by default: no writes, no commands, no shell.
- Project content is fenced as untrusted input.
- No network beyond the configured model provider; `--pr` may call `gh` to read
  PR metadata, but never uploads source or diff content.
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
  schema.py       # findings, assessment, validation, serialization
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
- **Assessment never changes an exit code.** A review that would pass without
  `--assess` still passes with it.
- **Intent honesty:** 100% of assessments either cite a real intent artifact or
  mark `intent_source: "inferred"` with explicit assumptions.
- **Worth-it corpus:** seeded "should not have been built", "good change", and
  "missing intent" cases. First-release target: ≥60% agreement with the human
  labels; false `reconsider` on good changes ≤20%, tightening to ≤10% by v1.
- `--assess` adds at most 15% wall-clock time to the local 7B target; it is off
  for working-tree reviews by default.

---

### 4.2 Editable review harness

**Outcome:** a team can change how Sidekick reviews code without forking the
agent — and without redefining the agent.

**Status (2026-11-11):** shipped in `v0.32.0` (#420).

#### Definition

The review harness is a **policy pack inside Sidekick's broader harness**, not
the whole harness. The core harness — loop, prompt assembly, doctrines
(grounding, tool honesty, challenge), approvals, audit, context budgeting, and
security gates — is shared by every workflow and is not owned by review.

The review pack contains:

- rules and guidance
- severity policy
- file include/ignore patterns
- context and diff budgets
- model profile
- output contract
- optional deterministic checks
- worth-it signals and assessment policy
- rule and assessment fixtures for evaluation

It cannot execute arbitrary code, define shell commands, silently disable
security checks, or override core doctrines.

#### Core vs pack

| Layer | Owner | Review pack may change it? |
|---|---|---|
| Agent loop, tool dispatch, approvals, audit | core | no |
| Grounding, tool honesty, challenge doctrine | core | no |
| SSRF/egress, protected paths, hard refusals | core | no |
| Review rules, severities, include/ignore | review pack | yes |
| Review budgets, model profile, output contract | review pack | yes |
| Worth-it signals, thresholds, assessment policy | review pack | yes |
| Rule and assessment fixtures | review pack | yes |

A new developer capability follows the same contract: it registers a pack and
reuses the core harness; it does not fork the loop, import private agent
symbols, or redefine the product. Review is the first pack, not the harness's
identity.

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
  Project pack loading requires trust, or an explicit
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
assess = "auto"        # auto | always | never; never gates CI
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
  - assessment mode, worth-it signals, and intent source
  - review-pack hash for reproducibility
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
rule requires one. Worth-it assessments get the same treatment: seeded diffs
with expected verdicts, plus negative cases where the right answer is "no
material concern".

#### Acceptance criteria

- Merge/precedence tests cover every layer and conflict case.
- Project layer cannot disable protected security rules or core doctrines.
- `--explain` output is deterministic and includes the review-pack hash.
- Invalid config produces an actionable error, not a traceback.
- A project rule cannot execute code, spawn a process, or make a network call.
- At least 10 built-in rules have positive/negative fixtures.
- The review pack cannot change approval behavior, tool dispatch, audit, or
  egress policy; a test asserts the core/pack boundary.

---

### 4.3 Code intelligence

**Outcome:** the agent can find and read code without interrupting the user for
every search.

**Status (2026-11-08):** `search_code` and `glob_files` shipped (#398, PR #407);
line-range `read_file` shipped (#399, PR #406); `repo_map` v1 shipped (#400, PR #412).

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
   - Python symbols via `ast`; other source languages get top-level
     definitions via line-anchored regexes.
   - Default 1,500 tokens (hard cap 8,000), with explicit token, node, and
     scan caps. Read-only, no approval, same path/ignore/symlink guards.

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

**Status (2026-11-11):** shipped in `v0.32.0` (#422–#423).

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

These are enablers for the whole agent, not review-only changes. Developer work
inherits the same loop, approval gates, and audit path; there is no second agent
loop for review or for later developer features.

#### Adaptive step budgets

- Coding and review tasks get more steps than chat.
- Local models: 10–15 steps by default.
- Frontier models: 30–40 steps for coding/review.
- Unknown models: 15 steps, not 5.
- `--max-steps` is a first-class CLI option.
- The final answer reports why the loop stopped: completed, budget exhausted,
  tool failure, or model error.

#### Provider abstraction

**Status (2026-11-12):** shipped in the `v0.33.0` milestone (#312, #431).

- Define a provider protocol for chat, streaming, tool calls, capabilities,
  context window, and cost.
- Implement OpenAI-compatible and Anthropic providers behind it.
- `run_agent` uses one loop.
- Remove private symbol imports from `anthropic_backend.py`.
- Add parity tests that run the same scenarios against both providers.
- Do this before advertising "review works everywhere."

What shipped: `sk.backends` defines the seam (`Turn`, `ToolCall`, `Backend`);
`sk.agent` owns the single turn loop and the OpenAI transport beside it;
`sk.anthropic_backend` keeps only the Messages API wire format. The parity
table in `tests/test_provider_parity.py` runs the same scenarios against both
providers, and a test enforces that no backend imports a private symbol from
`sk.agent`.

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
- Add 10–15 branch-level **worth-it cases** with stated intent: half are
  well-scoped good changes, half are overbuilt or solve the wrong problem;
  store the expected verdict and the evidence that justifies it.
- Store expected results in JSON; run offline in CI.

#### Metrics

| Metric | Target |
|---|---|
| Blocker/major recall | ≥60% first release, ≥75% v1 |
| False-positive rate on clean diffs | ≤20% first release, ≤15% v1 |
| Worth-it assessment agreement | ≥60% first release, ≥75% v1 |
| False "reconsider" on good changes | ≤20% first release, ≤10% v1 |
| Evidence validity | 100% (findings and assessment claims) |
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
- Worth-it assessment cases have expected verdicts and evidence fixtures.
- A rule or assessment change runs only its fixtures in PR CI and the full
  corpus nightly.
- A regression in recall, precision, or assessment agreement blocks the
  release.

#### Acceptance criteria

- Offline review corpus runs in CI in under five minutes.
- Nightly live eval produces a scoreboard with pass@1/latency/cost.
- A release candidate cannot ship with a corpus regression beyond the agreed
  threshold.

---

### 4.8 Adoption, docs, and distribution

**Outcome:** the project is discoverable and the public story matches the
product.

- **Add a developer section to the README and website:** lead with the
  combination — chat, code, and review without source leaving the machine — and
  keep the existing assistant story intact. A reader must come away knowing
  Sidekick is a broad local-first agent with a first-class review workflow, not
  a review-only tool.
- **Demo:** a 30–60 second terminal recording of `sk review --base main` on a
  real repo.
- **Benchmarks:** publish the review corpus results, the worth-it assessment
  agreement rate, and the local-vs-cloud comparison.
- **Docs:** a dedicated `docs/review.md` and `docs/review-harness.md` with
  examples, including when *not* to use review and how the assessment stays
  advisory.
- **Fix stale claims:** website hero test count (1,399 vs 1,504), website roadmap
  done-list (stops at 0.29.0), portfolio card (1,212 tests, 0.29.0, "8k
  installs").
- **Add `SECURITY.md`:** scope, supported versions, disclosure process, threat
  model link.
- **Add issue/PR templates:** bug report, feature request, review-rule proposal.
- **Add GitHub topics and a real description:** local-first, code-review, ollama,
  mcp, developer-tools.
- **Design partners:** recruit 3–5 developers with privacy-sensitive repos;
  observe real review runs; do not add telemetry in v1.
- **Positioning test:** the homepage, README hero, and docs index all name the
  assistant surfaces and the review workflow; no page claims review is the whole
  product.

**Acceptance criteria:** docs match the shipped feature set; website/portfolio
claims are current; 3 design partners have run `sk review` on real repos; a
reader can describe Sidekick as both a broad agent and a review tool.

---

## 5. Phasing and sequencing

Estimates assume 1–2 maintainers. Calendar time will stretch if part-time.

| Phase | Duration | Deliverables | Exit criteria |
|---|---|---|---|
| **0. Decisions and baseline** | 2–3 days | Lock positioning, scope, review-pack format, review schema; add corpus skeleton | Decisions signed off; no open blockers |
| **1. Review foundation** | ~2 weeks | Target resolver, diff pipeline, code search/read ranges, `sk review` MVP, Markdown/JSON output | Review works on a real repo; evidence validation passes; code search shipped |
| **2. Review pack and verification** | ~2–3 weeks | Layered review pack, `--explain`/`--check-config`, built-in rules, seeded corpus, project checks, `sk check` | Review-pack precedence tests pass; review corpus thresholds met; verification detects failures |
| **3. Runtime and platform** | ~2–3 weeks | Adaptive budgets, provider abstraction, git tools, sandbox/hooks/security CI, coverage uplift | Provider parity, sandbox tests, security scans green |
| **4. Evals and adoption** | ongoing | Nightly live eval, scoreboard, docs/demo, design partners, team/air-gapped groundwork | Public benchmark; 3 design partners; docs current |

**Two tracks.**

- **Assistant track (always on):** chat, code reading/editing, web, voice,
  memory, skills, MCP, TUI, daemon. Security and bug fixes ship here without
  waiting for the developer track.
- **Developer track (sequenced):** review foundation → editable review pack +
  verification → runtime/platform → evals/adoption → later developer rungs.

The developer track must not freeze or demote the assistant track. Release
notes and docs report both, and the full suite remains the regression gate for
both.

### 5.1 Developer capability ladder (post-v0.31, tentative)

Review is the first rung, not the ceiling. The next developer capabilities are
parked here so the roadmap grows in a deliberate direction without turning the
plan into a wishlist.

| Rung | Capability | Why it fits local-first | Status |
|---|---|---|---|
| 1 | **Review** — findings + advisory worth-it assessment | read-only, privacy-sensitive, editable rules | v0.31–0.32 |
| 2 | **Verify** — real checks and parsed diagnostics | runs locally; no false pass | v0.32 |
| 3 | **Navigate/explain** — repo map, search, codebase walkthrough | local model, no source upload | v0.31+ |
| 4 | **Guided change** — fix/refactor with checkpoints and checks | approval-gated, reversible | v0.33+ |
| 5 | **Ship** — git status/diff/commit/PR helpers | local repo, reviewed before push | v0.33+ |
| 6 | **Team** — shared rules, memory, skills | rules checked into the repo | later |

**Promotion test.** Before a new developer rung is scheduled, apply the same
worth-it questions the review layer asks of a diff:

- Does it deepen the local-first advantage (offline, privacy, weak hardware)?
- Is it usable without a cloud account, and safe read-only-first?
- Can its quality be measured with an offline eval?
- Is it a real workflow, or just parity with a hosted agent?
- Does it extend the assistant without narrowing it?

If the honest answer is "parity with cloud agents", it waits.

**Version mapping:**

- `v0.31.0` — shipped: review foundation (code intelligence + `sk review` MVP).
- `v0.32.0` — shipped: editable review pack + verification loop.
- `v0.33.0` — next: provider abstraction + platform hardening.
- `v0.34.0` — evals, benchmark, adoption.

`v0.33.0` is scoped tentatively in `ROADMAP.md`. Later versions remain tentative.

---

## 6. Release gates

A release is not ready unless:

- Review corpus thresholds are met or explicitly waived with a documented reason.
- 100% of findings have valid evidence.
- Harness precedence and protected-rule tests pass.
- Code search and line-range read tests pass.
- Verification cannot report a false pass.
- The worth-it assessment is advisory only; no exit code depends on it, and its
  evidence/intent invariants hold.
- `ruff`, `mypy`, and the full test suite pass.
- Coverage is at or above the current gate, with no regression in the targeted
  modules.
- No assistant regression: existing chat/code/web/voice/memory/MCP paths stay
  green; review does not narrow the harness.
- A review pack cannot disable core doctrines or protected security rules.
- Docs and release notes match the release.
- No known blocker in the review path.

---

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Review produces noisy, untrustworthy findings | Evidence validation, deterministic checks, conservative severities, seeded corpus, precision thresholds |
| Project rules inject prompts or weaken security | Untrusted by default, fencing, no execution, protected security rules, explicit trust flag |
| Large diffs exceed context or cost | Per-file chunking, content-hash caching, max files/findings, coverage report |
| Local 7B quality is too low | Hybrid deterministic + model review; default to smart model; publish honest benchmarks |
| Review identity swallows the assistant | Additive positioning and docs; dual-track release notes; assistant regression gate; review stays namespaced (`sk review`, `/review`) |
| Worth-it layer becomes a nag or blocker | Advisory-only invariant; default on for branch/PR targets only; one round; `--no-assess`; no CI gating; one-line pass for straightforward changes |
| Worth-it verdicts are noisy or wrong | Deterministic signals first, evidence validation, confidence labels, seeded assessment corpus, human-agreement tracking; labelled judgment, not a finding |
| Review pack becomes a second agent loop | Core/pack contract in §4.2; shared dispatch, approvals, audit; no private agent imports |
| Scope expands into general coding-agent parity | Developer ladder + promotion test in §5.1; review first; defer IDE/team/subagents |
| Provider divergence makes review inconsistent | Provider abstraction before multi-provider marketing; parity tests |
| Sandbox work delays the wedge | Ship read-only review first; sandbox is a prerequisite for autonomous verification, not review |
| Tests create false confidence | Live eval harness and corpus; offline tests only prove regression safety |
| Docs drift again | Gate version and test count, or remove hardcoded counts; docs review in release checklist |
| Adoption remains low | Additive positioning, demo, benchmark, topics, design partners; measure feedback, not stars alone |

---

## 8. Decisions (accepted for v0.31.0)

The recommendations below are the Phase 1 defaults. Revisit one only with a
documented reason in the PR that changes it.

1. **Is code review the flagship?**
   Recommendation: **it is the flagship developer capability, not the product
   identity.** It is the best fit for local-first, privacy, and editable rules,
   and it is the first developer workstream we make excellent. Sidekick remains
   a broad local-first terminal agent.

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

10. **Is there a worth-it layer?**
    Recommendation: **yes, as an advisory change assessment in review.** It is
    on by default for branch/PR targets, off for quick working-tree reviews,
    opt-out with `--no-assess`, and never gates CI.

11. **Does review narrow the product or the harness?**
    Recommendation: **no.** Review is a policy pack on the shared core harness.
    The assistant surfaces stay first-class, and new developer capabilities
    must pass the promotion test in §5.1.

---

## 9. First backlog

Items 1–7 shipped in `v0.31.0` (PR #411); items 8–10 shipped in `v0.32.0`
(#420–#423). Items 11–14 remain backlog for later phases.

1. [#398](https://github.com/Faisal-Fayaz/sidekick/issues/398) — Add `search_code` and `glob_files` tools with path-guard and fallback tests. **Shipped (PR #407).**
2. [#399](https://github.com/Faisal-Fayaz/sidekick/issues/399) — Add line-range support to `read_file`. **Shipped (PR #406).**
3. [#400](https://github.com/Faisal-Fayaz/sidekick/issues/400) — Add `repo_map` v1: tree, languages, Python symbols, token cap. **Shipped (PR #412).**
4. [#401](https://github.com/Faisal-Fayaz/sidekick/issues/401) — Build review target resolver and diff model. **Shipped (PR #411).**
5. [#402](https://github.com/Faisal-Fayaz/sidekick/issues/402) — Add finding schema, validation, and Markdown/JSON serialization. **Shipped (PR #411).**
6. [#403](https://github.com/Faisal-Fayaz/sidekick/issues/403) — Wire `sk review` and `/review` to the same engine. **Shipped (PR #411), with GitHub/Bitbucket PR posting ([#410](https://github.com/Faisal-Fayaz/sidekick/issues/410)).**
7. [#408](https://github.com/Faisal-Fayaz/sidekick/issues/408) — Add the
   advisory worth-it assessment layer: intent resolution, deterministic
   signals, assessment schema, `--assess`/`--no-assess`, and seeded cases.
   **Shipped (PR #411).**
8. Add review-pack loader, merge logic, `--explain`, and `--check-config`.
   **Shipped ([#420](https://github.com/Faisal-Fayaz/sidekick/issues/420), PR #425).**
9. Add built-in review rules and the first 10 seeded cases.
   **Shipped ([#421](https://github.com/Faisal-Fayaz/sidekick/issues/421), PR #426).**
10. Add project checks config, command runner, and diagnostic parsers.
    **Shipped ([#422](https://github.com/Faisal-Fayaz/sidekick/issues/422), PR #427); post-edit
    verification and `sk review --verify` shipped in
    [#423](https://github.com/Faisal-Fayaz/sidekick/issues/423) (PR #428).**
11. Add adaptive `max_steps` and `--max-steps`.
12. Add provider protocol and Anthropic parity tests.
    **Shipped (#312, #431): `sk.backends` is the seam, `run_agent` drives one
    loop, and `tests/test_provider_parity.py` runs both providers against the
    same expectations.**
13. Update README/website/portfolio; add `SECURITY.md` and issue templates.
14. Developer-ladder spikes (post-v0.31): `sk explain`, `sk fix`, `sk test`,
    `sk pr`; each gated by the promotion test in §5.1.

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
- `sk review --assess` gives an evidence-backed worth-it verdict that a
  developer finds useful, and never turns a shipping decision into an
  interrogation.
- `sk check` runs real project checks and feeds diagnostics back to the agent.
- Code search and line-range reads remove the need for approval-heavy shell
  searches.
- The agent has enough steps for developer work and a single provider loop.
- The public story is "a local-first terminal agent with first-class code
  review," not a review-only tool or a generic cloud-agent clone.
- The assistant remains whole: chat, code, web, voice, memory, skills, MCP, and
  approvals still work and are still documented as first-class.
