# Performance budgets

Sidekick's promise is a capable local agent on weak hardware with no API bill.
That is checkable only if the fixed cost of a turn is bounded. This page states
the budgets, the harness that measures them, and what is deliberately not a CI
gate.

## What is measured

`docs/probe-perf.py` assembles each surface's prompt with `build_messages` —
the exact same assembly the agent uses — and counts tokens with the shared
`sk.tokens.estimate_tokens` (4 characters per token, rounded up). It needs no
model, no key, no Ollama and no network.

```bash
uv run python docs/probe-perf.py
uv run python docs/probe-perf.py --json
```

It exits **non-zero on a breach**, so it is a CI gate in the `lint` job.

| Component | Budget | Why |
|---|---|---|
| system prompt | 2,400 tokens | Instructions, rules and the real sysinfo snapshot. A bloat here is paid on *every* turn. |
| tool schema | 3,000 tokens | 27 built-in tools plus any plugin/MCP extras; stable and name-sorted for cache reuse (#276). |
| fixed total (system + injected context + tools) | 5,600 tokens | The floor every surface pays before history. |
| chat with 10 history pairs | 9,000 tokens | The fixed floor plus what the 3,000-token history budget actually lets through (#308). |

The per-surface table in `SURFACE_BUDGETS` gives every surface the fixed ceiling
so a regression names the surface that broke it.

## Snapshot on this machine

Produced by the harness on 2026-11-18, checked into `docs/perf.md` so a change
is visible in review:

| surface | system | tools | fixed | total |
|---|---:|---:|---:|---:|
| chat | 2,133 | 2,860 | 4,993 | 4,994 |
| run | 2,133 | 2,860 | 4,993 | 5,000 |
| review | 2,133 | 2,860 | 4,993 | 4,999 |
| explain | 2,133 | 2,860 | 4,993 | 4,999 |
| check | 2,133 | 2,860 | 4,993 | 4,997 |
| serve (read-only) | 2,140 | 2,860 | 5,000 | 5,007 |
| chat + 10 history pairs | 2,133 | 2,860 | 4,993 | 7,916 |

**These are a snapshot, not a promise.** The token estimator is an
approximation and the system prompt includes a live hardware snapshot, so exact
numbers vary by machine. The budgets above have roughly 10% headroom; the gate
catches a step change, not a few tokens of drift.

## Latency

Latency is machine- and model-dependent, so it is **not** a CI gate. The live
harness `docs/probe-evals.py` reports wall time per task and time to first token
alongside pass@1 and tokens; its numbers live in
[`docs/eval/results.md`](eval/results.md) with the same snapshot disclaimer.

What is expected, as an order of magnitude rather than a bound:

- a greeting with no tools is near-instant (no model call is needed for the
  fast path where it applies);
- a grounded turn costs one sysinfo tool call plus one model turn;
- review, explain and check add one or two read-only tools;
- the IDE extension's status command is a single local `/health` request, while
  review/explain/check add one `/turn`.

If a surface's latency regresses by more than a small factor, reproduce with
the live harness before changing a budget — the usual cause is a tool call that
started doing network I/O, not prompt size.

## Why this is also a correctness gate

The history budget is computed from the *assembled* prompt, not history in
isolation (#308). If the fixed part grows, history silently shrinks and the
model loses the thread of a long session. Pinning the fixed overhead protects
conversation quality, not just speed. The untrusted-content fence is also part
of the fixed cost, so a new auto-context source cannot appear unnoticed.

## Tests

`tests/test_perf.py` pins the component overhead tighter than the published
budgets (system ≤ 2,300, tools ≤ 2,950, fixed ≤ 5,200), checks that history fits,
and asserts the harness exits 0 normally and 1 with a named breach. A
deliberately oversized prompt fails both the harness and the test.

## See also

- [Benchmark results](eval/results.md) — live pass@1, wall time and tokens.
- [The SDK and the local API](sdk.md) — the other surfaces this prompt serves.
