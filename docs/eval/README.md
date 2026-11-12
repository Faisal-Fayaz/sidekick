# Live evals

The offline suite (`tests/`) proves that Sidekick does not regress. It cannot
prove that a *model* still answers, that a turn is still fast, or that a turn
still costs what it cost. That is what this directory is for: a fixed task set,
run against a real model, with the numbers written down.

## The task set

`tasks.json` — five fixed prompts. They do not change between runs: a number
that moves because the task set moved is not a number.

| task | what it probes |
|---|---|
| `p1_greeting` | the greeting fast path (no model call) |
| `p2_instruction` | instruction following — does the answer keep the shape it was given |
| `p3_grounding` | grounding — the answer has to come from `sysinfo`, not from memory |
| `p4_tool_turn` | the tool turn — the agent has to act, not describe |
| `p5_network` | the network task — isolated, and only run when explicitly allowed |

Verdicts follow `docs/probe-routing.py`: `OBEYED`, `PARAPHRASED`, `FAILED`.

## Running it

```bash
python3 docs/probe-evals.py                     # local fast tier
python3 docs/probe-evals.py --tier smart        # local smart tier
python3 docs/probe-evals.py --task p3_grounding # one task
python3 docs/probe-evals.py --allow-network --network
python3 docs/probe-evals.py --repeat 5 --k 5    # pass@1 and pass@5
python3 docs/probe-evals.py --json              # machine-readable
```

`--repeat N` draws N samples per task, so the table can report how often a
task was solved (`solved` column) instead of only whether it was. `--k` picks
the k for pass@k, using the unbiased estimator `1 - C(n-c, k) / C(n, k)`:
pass@1 is the plain rate, and pass@k with k = n asks "was it ever solved".

Cloud tiers need a key and a spend cap:

```bash
SIDEKICK_API_KEY=sk-or-... python3 docs/probe-evals.py \
    --provider openrouter --model openai/gpt-4o-mini \
    --price-per-mtok 0.15 --spend-cap 1.00
```

The output is three paste-ready tables — **Speed** (wall, TTFT), **Quality**
(verdict, tools used) and **Cost** (tokens, and the price when one is given) —
plus a one-line summary. That is what goes into an issue or a release note.

It is not part of `pytest`: it spends model time and money. The task set
*is* checked by the offline suite (`tests/test_eval_tasks.py`), so it cannot
rot unnoticed between live runs.

## Nightly

A nightly run keeps the numbers honest, because model updates land without a
commit. Suggested cron, on a machine with Ollama running:

```cron
30 6 * * * cd /path/to/sidekick && /usr/bin/python3 docs/probe-evals.py --tier fast --json > /tmp/sk-eval-fast.json 2>&1
```

Keep the JSON, not just the table: the history is what makes a regression
legible ("the smart tier lost 2s per turn after the router change"). The
leaderboard section (#188) renders from those runs.

## Cost

Every run is a turn, and a turn is tokens. The default is the local tier,
which costs nothing but time; cloud tiers take `--price-per-mtok` so the cost
column is a number rather than a shrug, and `--spend-cap` so a runaway task
cannot outlive its budget.
