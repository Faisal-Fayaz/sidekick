# Review

`sk review` is Sidekick's evidence-backed code review surface. It is a
developer feature, not a separate product: the same assistant, approvals,
models, and audit trail remain available.

Review is read-only. It never edits a branch, applies a suggestion, or pushes a
commit. The only external write is a PR comment, and only when you review a
pull request with working credentials.

## Targets

```bash
sk review                     # working tree vs HEAD
sk review --staged            # staged changes only
sk review --base main         # branch vs the merge-base with main
sk review --commit HEAD       # one commit vs its parent
sk review --pr 123            # pull request (GitHub or Bitbucket)
sk review --path src/         # limit the diff to a path
```

`/review` inside a session uses the same engine. The legacy positional form is
still accepted: `/review main` means `sk review --base main`.

## What a finding contains

Each finding must name a file, a new-file line that is inside the diff, and an
exact evidence quote that appears in the file (or in the diff for deleted
code). Findings that fail those checks are dropped and counted — they are not
shown with a lower confidence. Every finding also has:

- severity: `blocker`, `major`, `minor`, `nit`
- category: `correctness`, `security`, `performance`, `maintainability`,
  `tests`, `style`
- problem, impact, and a concrete suggested fix
- a confidence and an optional stable `rule_id`

A small set of deterministic checks runs without a model (for example Python
`eval`/`exec` and `subprocess(..., shell=True)`). Deterministic findings go
through the same evidence validation.

## Worth-it assessment

For branch, commit, and PR reviews, Sidekick adds an advisory assessment before
the findings. It resolves the stated intent from, in order:

1. `--intent`
2. the PR title/body
3. commit messages in the range
4. the branch name

It never invents intent. If none is available, the assessment records that as
an assumption. Deterministic signals (tests changed or missing, dependency
files, migrations, security-sensitive paths, diff size, skipped files) are
computed from the diff, not from the model.

The assessment can ask up to three evidence-backed questions, propose a grounded
alternative, and give a verdict: `proceed`, `proceed-with-changes`, or
`reconsider`. It is advisory only: it never changes the exit code, the SARIF
output, or whether the review runs.

- `--assess` forces it on.
- `--no-assess` disables it.
- The default is on for `--base`, `--commit`, and `--pr`, off for working-tree
  and staged reviews.

## Posting to pull requests

`sk review --pr N` posts comments automatically when credentials are available:

- **GitHub** uses `gh` when it is installed and authenticated. A `GH_TOKEN` or
  `GITHUB_TOKEN` fallback uses the REST API. The review body and valid inline
  file:line comments are posted; an invalid inline anchor falls back to a
  summary comment rather than losing the review.
- **Bitbucket Cloud** uses the REST API. Set `BITBUCKET_USERNAME` and
  `BITBUCKET_APP_PASSWORD`, or `BITBUCKET_TOKEN` for a bearer token. The summary
  comment is posted first; inline comments are best-effort.
- **Bitbucket Server/Data Center** is detected from the remote host and uses
  its REST 1.0 API with the same credentials.

`--no-post` disables posting. `--post` requires a PR and exits non-zero if the
comment could not be posted. Missing credentials without `--post` are a note,
never a failed review.

Posting is logged through the same audit ledger as other tool actions. The
posted text comes from validated findings and the advisory assessment only — it
never includes the raw diff, secrets, or unfiltered model output.

## CI

```bash
sk review --base origin/main --fail-on major --sarif > review.sarif
```

`--fail-on` accepts `blocker`, `major`, `minor`, `nit`, or `never` (the
default). `--json` emits the full machine-readable report. Review warnings
(model chunk failures, skipped files, truncation) are in the report; they do
not erase the findings that were validated.

## Limits

- The diff is capped (200 files / 400 KB by default). Truncation is reported
  and coverage is marked partial.
- Large diffs are split into model chunks; cross-file reasoning is therefore
  limited to what fits in each chunk.
- The review prompt/rules are built in for now. The editable review pack and
  project-level `review.toml` are planned follow-ups; the engine boundary is
  already in `src/sk/review/`.
- Review uses the configured provider/model. Point it at a local model with
  `sk model` or `--model`; no source leaves the machine unless the model
  provider is remote.
