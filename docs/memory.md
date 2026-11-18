# Memory: ranking, merging and proposals

Sidekick remembers facts in a local SQLite database and finds them with FTS5.
That still holds. On top of it, recall is now ranked by three signals, saving
merges near-duplicates instead of stacking them, and a turn can produce
*proposals* — candidate facts the agent may offer to remember, never a silent
write.

## Recall ranking

A candidate's score is:

```text
FTS relevance × recency × importance
```

- **Relevance** comes from FTS5 (prefix, typo-tolerant). An FTS miss falls back
  to the original substring scoring, so recall keeps working if the index is
  unavailable.
- **Recency** is exponential decay with a half-life, default **30 days**. A fact
  last used 30 days ago counts half as much as a fresh one.
- **Importance** grows with the number of times a fact has been recalled:
  `1 + log2(1 + uses)`. Recalling a fact updates its use count and last-used
  time, so useful facts stay near the top.

The practical effect: a stale, never-used fact no longer outranks a fresh one
simply because its text matched slightly better.

### Tunables

In `~/.sidekick/config.toml`:

```toml
memory_half_life_days = 30
memory_dedup_threshold = 0.85
```

or in the environment (`SIDEKICK_MEMORY_HALF_LIFE_DAYS`,
`SIDEKICK_MEMORY_DEDUP_THRESHOLD`), which wins over the file. Out-of-range or
non-numeric values fall back to the defaults above. These settings are
**global only** — a repository cannot change how your memory is ranked or
merged.

## Dedup and merge on save

Saving a fact compares it with existing facts in the same namespace after
lowercasing, collapsing whitespace and removing punctuation. If the similarity
is at or above the threshold (default 0.85), the existing row is **updated**
rather than a new one inserted:

- the content becomes the new phrasing;
- the timestamp refreshes;
- the use count is carried forward and incremented.

The command reports which happened:

```text
Remembered (7 total).
Merged with a similar memory (7 total).
```

Distinct facts are untouched. The merge is namespace-scoped, so a project fact
never merges into a global one.

## Proposals, not silent writes

```bash
sk memory-proposals "Remember that deploys happen on Fridays."
sk memory-proposals "My staging URL is staging.internal."
```

This prints candidate facts and a reminder that nothing was saved. The extractor
is deliberately narrow — `remember that …`, `my …`, `I prefer/use/work on …` —
and returns data only. It never opens the store, and there is no flag that makes
it write. To actually save a proposal, use the normal path:

```bash
sk remember "deploys happen on Fridays"
```

That is a tool call like any other, so it goes through the same approval and
audit ledger as every other write. Proposals exist to be *offered* by an agent
turn; the user still decides.

## Storage and compatibility

The schema moves from version 5 to 6 by adding two columns to `memories`:
`uses INTEGER NOT NULL DEFAULT 0` and `last_used REAL NOT NULL DEFAULT 0`. It is
purely additive — existing rows, namespaces and FTS entries are preserved, and
an older database is migrated on first open. A database from a newer Sidekick
is left untouched.

The team layer (`~/.sidekick/team/`, `.sidekick/team/`) is a separate, file-based
memory namespace described in [team.md](team.md); these tunables govern the
SQLite memory that `remember`/`recall` use.

## Tests

`tests/test_memory_rank.py` covers the pure ranking and similarity functions,
config precedence and invalid values, a project file that cannot set policy,
merge-on-save, the fresh-used-beats-stale ordering, recall without an FTS index,
and the guarantee that proposals never touch the database.
`tests/test_store_migrate.py` covers the additive v5→v6 migration and data
preservation.

## See also

- [Team memory and shared skills](team.md) — the file-based, shared layer.
- [The SDK and the local API](sdk.md) — the same memory tools from other surfaces.
