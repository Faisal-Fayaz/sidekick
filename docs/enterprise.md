# The local enterprise surface

The open core stays local. This surface gives a team three things without a
cloud service: a readable audit dashboard, an operator tool policy, and
identity attribution when an upstream system supplies it.

## Audit dashboard

The ledger is always queryable:

```bash
sk audit --session default --format json
sk audit -n 50
```

For a shareable, self-contained view:

```bash
sk audit report --format md --out /tmp/audit.md
sk audit report --format html --out /tmp/audit.html
sk audit report --session default
```

With no `--out`, the report is written to `~/.sidekick/audit-report.md` (or
`.html`). It contains:

- totals: runs, allowed, denied, failed, taint decisions, egress;
- breakdowns by session and by actor;
- the last 500 rows with tool, target and decision.

The report is **fully offline**: the HTML has no scripts, remote CSS, fonts or
image URLs, so opening it cannot make a request. Targets are redacted for keys,
tokens, secrets and URLs before rendering. Malformed rows are skipped rather
than counted.

## Operator tool policy

`~/.sidekick/policy.toml` is the only policy file that is read:

```toml
deny_tools = ["shell", "git_push", "web_search"]
```

A denied tool is refused at the gate **before** approval, in every mode —
including `--yes` — and the refusal is written to the ledger as
`policy=deny <tool>`. A broken file means no policy, not more power. A
`.sidekick.toml` in a repository cannot set, widen or narrow this list; that is
the same global-only rule used for egress, search keys and telemetry.

This is a deny list, not a permission widen: it can only take tools away from
the user's own installation.

## Identity attribution

```bash
export SIDEKICK_ACTOR="alice@example.com"   # or let your IdP set it
export SIDEKICK_TEAM="platform"
```

Those values are stamped into every new audit row's `actor` field and shown in
the report's actor breakdown. With neither set, the field stays empty and
existing rows are unchanged. The values are capped at 120 characters.

**There is no built-in SSO in the open core.** Sidekick does not talk to an
identity provider, hold a session cookie, or verify a token. An enterprise
deployment supplies identity from its own IdP through the environment before
launching `sk` (or `sk serve`), and is responsible for that integration. This
boundary is deliberate: adding an IdP would mean adding a network dependency
and a new credential store to a tool whose promise is local-first.

## What this is not

- Not a central log collector. Reports are files on the user's machine;
  nothing is uploaded.
- Not a policy server. `deny_tools` is local operator config, evaluated at the
  gate on every call.
- Not a replacement for the approval gate, sandbox, taint policy or egress —
  each still runs independently.

## Tests

`tests/test_enterprise.py` covers identity defaults and env values, global-only
policy loading, gate enforcement with an audit row, actor stamping, Markdown
and HTML rendering with redaction, empty and malformed rows, session filtering
and the `sk audit report` CLI alongside the unchanged `sk audit` command.
`tests/test_store_migrate.py` covers the additive v7 `tool_runs.actor` column.

## See also

- [Threat model](threat-model.md) — the boundaries this data crosses.
- [Taint-aware content](taint.md) — the other decisions recorded in the ledger.
- [Crash telemetry](telemetry.md) — opt-in and local by default.
