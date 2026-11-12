# Checks

`sk check` runs the commands a repository declares in `.sidekick/checks.toml`
and turns their output into compact, structured diagnostics. It is the same
shell path the agent uses: hard refusals, hooks, approval, checkpoints, and the
audit ledger all still apply.

Checks are evidence, not a trust shortcut. A command that times out, cannot
spawn, or is denied reports `unknown` — never `pass`.

## Quick start

```toml
# .sidekick/checks.toml
schema = 1

[checks]
lint = "ruff check ."
typecheck = "mypy src"
test = "pytest -q"
```

```bash
sk check                              # project checks are untrusted by default
sk check --trust-project-checks       # load and run them (each command still asks)
sk check --trust-project-checks --yes # approve check commands for this run
sk check --changed                    # only checks/files affected by the diff
sk check --json                       # machine-readable report
```

Or trust repo-supplied configuration for the process:

```bash
SIDEKICK_TRUST_REPO=1 sk check --yes
```

## Trust

A cloned repository must not be able to run shell commands just by being
cloned. `.sidekick/checks.toml` is therefore ignored unless you explicitly opt
in, using either:

- `--trust-project-checks` on `sk check` (or `--trust-project-rules` on
  `sk review` for review packs), or
- `SIDEKICK_TRUST_REPO=1` in your own environment.

The environment variable is read from the outer environment only; repository
config cannot set it. An untrusted checks file is reported as `unknown` with
exit code `2`, and **no commands are loaded or run**.

Trust controls loading, not execution. Every loaded check still goes through
the normal shell approval gate. Approve interactively, with `--yes`, with
`--allow shell:<prefix>`, or with `approved_commands` in your user config.
`--deny` always wins. Every run — approved, denied, or hard-blocked — is
written to the audit ledger under the `--session` name (default `check`).

Programmatic callers get the same guarantee: `run_checks()` refuses to run
when no approval callback is supplied.

## Schema

Both TOML shapes are accepted. The map form is convenient for simple commands:

```toml
schema = 1

[checks]
lint = "ruff check ."
test = "pytest -q"
```

The table/array form exposes every field:

```toml
schema = 1

[[checks]]
id = "lint"
command = "ruff check ."
kind = "lint"
parser = "ruff"
timeout = 60
applies_to = ["src/**/*.py", "tests/**/*.py"]
changed = "ruff check {files}"

[[checks]]
id = "typecheck"
command = "mypy src"
parser = "mypy"
```

`schema = 1` is required. Unknown keys, duplicate IDs, empty commands, invalid
globs, and unsupported schema versions are errors; a malformed file blocks all
execution rather than running a half-understood subset.

| Field | Default | Meaning |
|---|---|---|
| `id` | map key | Stable name used in reports and diagnostics |
| `command` | required | Shell command, run through the normal approval gate |
| `kind` | inferred | `lint`, `typecheck`, `test`, `build`, `format`, or `other` |
| `parser` | inferred | `auto`, `pytest`, `ruff`, `mypy`, `eslint`, `tsc`, `go-test`, or `raw` |
| `timeout` | `120` | Seconds, `5`–`120` (the shell tool's own contract) |
| `applies_to` | parser default | Repo-relative globs used by `--changed` and targeted verification |
| `changed` | `command` | Template for `--changed`; `{files}` is replaced with shell-quoted paths |

Parser and kind inference look at both the check ID and the command. `pytest`,
`ruff`, `mypy`, `eslint`, `tsc`, and `go test` are recognized; anything else
falls back to `raw`, which passes or fails on the exit code and preserves
unparsed output.

Default `applies_to` values are language-scoped: Python files for pytest/ruff/
mypy, JS/TS files for eslint, TypeScript files for tsc, and Go files for
`go test`. `raw` checks default to `**/*`.

## Post-edit verification

After the agent edits files, it runs a fast verification pass before shipping
the answer:

1. A syntax check for the edited files: Python and `.pyi` via `tokenize.open` +
   `compile`, JSON via `json.load`, TOML via `tomllib`, shell via `bash -n`,
   and JavaScript/`.mjs`/`.cjs` via `node --check`. TypeScript relies on the
   configured `tsc` check.
2. The project's configured `lint` and `typecheck` checks for the affected
   files, when a trusted `.sidekick/checks.toml` provides them.

Every command still goes through the same shell approval and audit path as
`sk check`. A failing configured check is a **failed verification**, not a
generic tool error. The compact diagnostics are fed back to the model for up to
two targeted repair rounds (`SIDEKICK_VERIFY_REPAIRS`, default `2`; `0`
disables verification).

Missing tools, timeouts, denials, and an untrusted checks file are `unknown`,
never `pass`. Unknown-only results do not trigger a repair round; they are
appended to the final answer so the user knows verification did not happen.

Verification runs on every provider path — OpenAI-compatible endpoints and the
native Anthropic Messages API behave the same way here, so switching provider
does not quietly switch verification off.

```bash
sk run "fix the failing test" --trust-project-checks
sk run "fix the failing test" --trust-project-checks --verify-full
SIDEKICK_VERIFY_FULL=1 sk run "fix the failing test"
```

- `--trust-project-checks` (or `SIDEKICK_TRUST_REPO=1`) loads the project
  checks for verification. Without it, only the built-in syntax checks run and
  a declared but untrusted checks file makes the verdict `unknown`.
- `--verify-full` (or `SIDEKICK_VERIFY_FULL=1`) runs every configured check,
  including tests and builds. The default runs only `lint` and `typecheck` for
  the edited files.

### Review verification

`sk review --verify` runs the same verification for the files in the reviewed
diff and adds a separate `verification` section to the Markdown and JSON
report. It does not turn check diagnostics into review findings.

```bash
sk review --staged --verify --trust-project-rules
sk review --staged --verify --yes --json
```

Verification commands use the normal approval gate. `--yes`, `--allow`, and
`--deny` have the same meaning as on `sk run`; `--deny` wins. A `fail` or
`unknown` verification makes `sk review --verify` exit `1`, even when the
`--fail-on` finding threshold is not met.

## Running just the tests

`sk test` runs the project's tests through the same pipeline: the same approval
gate, the same hooks, the same audit ledger, the same parsers, the same exit
codes.

```bash
sk test                        # the declared test check, or a detected one
sk test --yes --json           # machine-readable report
sk test --command "pytest -q tests/cli"   # an explicit command
```

The command comes from the project, never invented: the check of kind `test` in
`.sidekick/checks.toml` wins, and otherwise it is detected from a `Makefile`
target, a `package.json` script, `Cargo.toml`, `go.mod`, or a Python project.
Without either, `sk test` says so and exits `2` rather than reporting a green
suite.

A test runner that cannot be found is `unknown`, never a pass.

## Fixing, and proving it

`sk fix "<what is wrong>"` is the guided-change rung: the agent investigates
and edits under the normal approval gate, its edits are checkpointed (so
`/rewind` can undo them), and the changed files are verified afterwards.

```bash
sk fix "the parser drops the last token"
sk fix "the parser drops the last token" --trust-project-checks --json
```

What the command adds over an ordinary turn is the **verdict**: the pass, fail
or unknown of the verification is reported as a verdict rather than left in
prose, and a change that did not verify exits `1` instead of shipping as
"done". The checks run once — the agent owns the repair loop, so a repair
round keeps the conversation's context.

## Diagnostics and statuses

Parsers are pure functions over captured output and never invent a pass. Each
diagnostic carries file, line, column, severity, code, message, source, and the
check ID. The compact report shows the first ten per check; `--json` includes
all of them (capped by `--max-diagnostics`, default 50).

| Status | Meaning |
|---|---|
| `pass` | Exit code 0 and no unknown condition |
| `fail` | Exit code non-zero and the failure is reportable |
| `unknown` | Timeout, spawn failure, missing tool, denial, hard block, output overflow, untrusted/config error, or an unparsed failure |
| `skipped` | `--changed` selected no files relevant to this check |

Overall status is `unknown` if any check is unknown, otherwise `fail` if any
failed, otherwise `pass`. Unknown is never collapsed into pass.

If a configured parser does not understand a failing tool's output, the check
stays `fail`, is marked `parsed: false`, and includes the raw output as a
fallback.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Every check passed (or nothing needed to run) |
| `1` | At least one check failed or is unknown |
| `2` | Config error or untrusted project checks |

## Flags

| Flag | Effect |
|---|---|
| `--changed` | Derive files from `git diff --diff-filter=ACMR HEAD` plus untracked files; deleted files are excluded |
| `--json` | Emit one JSON document with counts, results, diagnostics, and exit status |
| `--yes`, `-y` | Auto-approve check commands |
| `--allow LIST` | Auto-approve tools or shell prefixes, e.g. `--allow shell:ruff` |
| `--deny LIST` | Never approve listed tools; deny beats every allow path |
| `--trust-project-checks` | Load the repo's checks file |
| `--check-config` | Validate and print issues, then exit (`2` only on errors) |
| `--explain` | Print the loaded checks and their effective settings |
| `--max-diagnostics N` | Cap diagnostics per check (default `50`) |
| `--session S` | Audit session name (default `check`) |

## `--changed`

With no `changed` template, a matching check runs its normal command. That is
the right default for tools that already inspect the repository.

With a template, `{files}` is replaced by shell-quoted matching paths:

```toml
[[checks]]
id = "lint"
command = "ruff check ."
changed = "ruff check {files}"
applies_to = ["src/**/*.py", "tests/**/*.py"]
```

The substituted command is capped at the shell tool's 2000-character limit;
if some paths do not fit, the report says how many were omitted. Checks with no
matching files are `skipped`.

## Safety boundary

Checks are commands, and commands remain commands:

- The shell hard-block list is checked **before** approval; `--yes` cannot
  waive it.
- PreToolUse and PostToolUse hooks still run.
- Checkpoints, approval audit rows, and the egress/safety rules are unchanged.
- A check timeout is bounded by the shell tool (`5`–`120` seconds), so config
  validation uses the same ceiling.
- `raw` fallback still reports the exit code; it never turns an unparsed
  failure into a pass.

## CI and agents

`sk check --json` is stable enough to parse in CI, and the exit code already
matches the pass/fail/unknown contract. For an agent workflow, prefer
`sk check --changed` after edits and a full `sk check` before commit.

See [Review](review.md) for the diff-review engine and
[the developer plan](developer-plan.md#44-verification-loop)
for the design rationale.
