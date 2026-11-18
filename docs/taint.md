# Taint-aware content policy

The untrusted fence in `src/sk/trust.py` tells the *model* that a block is data.
It is advisory: a page cannot be trusted simply because the prompt says not to
trust it. This module carries provenance one step further, to the **tool gate**,
so untrusted text cannot quietly become an action.

## How a turn is marked

`build_messages` assembles all outside content into fenced, provenance-labelled
blocks — web pages, search results, local file listings, repo documentation and
project team data. Each of those sources now also calls `taint.mark(text,
source)`, which records a fingerprint of the exact text in a per-turn
`ContextVar`. Marking is reset at the start of every turn, so a concurrent local
API session cannot inherit another turn's taint.

Nothing about the visible prompt changes: the fence text, the labels and the
cache-stable prefix are the same as before.

## Decisions at the gate

`taint.decide(name, args)` looks for fingerprints of marked content in a tool's
arguments and returns one of:

| Decision | When | Effect |
|---|---|---|
| `allow` | No tainted arguments, or a plain read of a normal path | Existing behaviour; no extra prompt. |
| `approve` | A write, shell, network fetch (`read_url`/`web_search`) or any already-gated tool uses tainted text | Requires an approval callback when one exists. |
| `deny` | A tainted argument names a secret-adjacent path or value (`.ssh`, `id_rsa`, `.env`, `serve.token`, key/credential names) | Refused even in auto-approve mode; nothing executes. |

The gate runs **after** mode denials, the sandbox check, missing-argument checks
and the irreversible-refusal blocklist. It is an additional gate, not a
replacement for any of them. A denial names the origin and the reason in the
message the model sees.

## The audit row

Every decision is recorded in the existing `tool_runs` table's `target` field —
no schema change:

```text
taint=approve origin=fetched web page read_url|url=...
taint=deny origin=repo documentation read_file|path=~/.ssh/id_rsa
```

`sk audit --session <name>` shows it alongside every other tool decision. An
allowed tainted read is marked too, so a reviewer can see that a path came from
outside content even though the read was fine.

## What it does not do

- It does not make prompt injection impossible. A model can still be persuaded
  that something it read is important; the gate only controls what it can
  *execute* with tainted arguments.
- It does not taint a whole conversation. Marks are fingerprints of fenced
  strings in one turn, and short strings are ignored to avoid matching
  everything.
- It does not override the user's explicit `--yes`/auto-approve for normal
  tools; secret-adjacent denials are the exception.
- It does not inspect file *contents* a tool returns before the next turn —
  those are fenced again by `build_messages` and marked then.

## Tests

`tests/test_taint.py` covers no-taint behaviour, origin matching on a snippet,
approval for network and write tools, secret-path denial, normal reads, the
audit target format, provenance recorded by `build_messages`, and two
`_gated_dispatch` integrations (approval and denial with audit rows).

## See also

- [Threat model](threat-model.md) — where this sits among the other boundaries.
- [Egress policy](egress.md) — the network gate that still runs independently.
- [Sandbox](sandbox.md) — the OS layer beneath the approval gate.
