# Golden files

A golden is a committed file holding the exact output a turn produces. It
exists so that a behaviour change shows up as a **reviewable diff in the same
commit**, instead of as a silent change in what the user sees.

The bar for adding one — all four, not one:

1. **It is a top-traffic turn.** The help reply, a refusal, a grounding
   block, the review prompt, the tool-call fallback. Not every function needs
   a golden; the ones people actually see do.
2. **It locks a behaviour or a past bug.** The manifest entry names which.
   A golden that only records "whatever it does today" is snapshot noise, and
   snapshot noise is how a suite stops being read.
3. **It is deterministic and offline.** No model calls, no network, no clock.
   Volatile values (dates, paths, machine facts) are masked or stubbed, not
   tolerated.
4. **It is cheap.** The whole suite runs in well under a second. A golden
   that costs a model call belongs to the live harness (#283), not here.

## Using them

```bash
uv run pytest tests/test_golden.py -q          # check
uv run pytest tests/test_golden.py -q --update-goldens   # rewrite from current behaviour
```

`--update-goldens` is the only way a golden changes, and the change lands in
the same commit as the behaviour change — that is the point. Read the diff
before you commit it: a golden that changed for a reason you cannot explain
is a bug report about itself.

## The manifest

`manifest.json` lists every golden: the file it lives in, what it locks, and
the issue it came from. The manifest is the source of truth — a file in this
directory that the manifest does not name is not run, and a manifest entry
without a producer fails the suite.
