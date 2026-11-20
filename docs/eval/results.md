# Benchmark results

Public numbers, so "great on weak hardware" is checkable rather than folklore
(#157). Every number here comes from the harness in `docs/probe-evals.py` over
the fixed task set in `docs/eval/tasks.json`.

## Methodology

**The task set is fixed.** The five original seed prompts are still there, and
the set now has **67 prompts** across six categories (chat, grounding, tool,
code, reasoning, network). Each task carries `category` and `difficulty` for
scoring; `kind` still drives execution, so network tasks remain isolated. The
network tasks are excluded unless `--allow-network` is passed, so a default
run touches no network. 64 tasks run by default.

Inspect the set without a model: `python3 docs/probe-evals.py --list`.
Run one category: `--category code`. Filter by id: `--task c_code_01`.

**The verdict is per task**: `OBEYED` / `PARAPHRASED` / `FAILED`, the style of
`docs/probe-routing.py`. A task that needed a tool and never called it is
FAILED whatever the answer says — an answer without the grounding is a guess,
and a guess is not a pass.

**What is measured**, per task and per target:

| column | what it is |
|---|---|
| pass@1 | the fraction of samples solved, averaged over the task set |
| wall/task | mean wall-clock seconds per task, end to end (tool calls included) |
| tokens | estimated tokens, prompt plus answer |
| cost | metered targets: what the tokens cost at the quoted price. Local: **no API bill** — the cost is electricity, which is not on the invoice |

**The numbers are a snapshot, not a promise.** They were produced on one
machine with one set of models; a different machine, a different quantisation,
or a different provider version moves them. The point is the *shape*: what a
local model does against a metered one, at what latency, for what money.

**Capability score.** `--score` adds per-category pass@1 and a weighted
0–100 score: grounding 20%, tool 25%, code 25%, reasoning 20%, chat 5%,
network 5%. The weights favour the categories that predict real engineering
work; network is low because it is isolated by default. The score is
**relative to this fixed set** — it is not a claim of frontier parity without
head-to-head baselines, and the harness prints that caveat.

**Baselines.** Compare targets in one run with the same tasks and repeat count:

```bash
python3 docs/probe-evals.py --compare --repeat 3 --score
python3 docs/probe-evals.py --compare --target ollama:qwen3:4b \
  --target openrouter:openai/gpt-4o-mini@0.15 --repeat 3 --score
```

`--target` is `provider:model[:price_per_mtok]` and is repeatable. The
comparison table keeps the cost column so a metered baseline is honest about
what the local tier does not bill. Publish the per-category scores beside the
pass@1 table; a single overall number hides where an agent is weak.

**Re-running per release.** The command is the same every time:

```bash
python3 docs/probe-evals.py --compare --repeat 3 --score
```

> **Historical numbers below:** the table was produced with the original
> five-task set (four tasks per run). It is kept for provenance, but it is **not
> comparable** to scores from the expanded 67-task set. A new snapshot with the
> expanded set and named baselines is the next measurement to publish here.

## The measurement

| | |
|---|---|
| when | 2026-11-12 |
| sidekick | 0.33.0 (this tree) |
| machine | AMD Ryzen 7 4800H (8c/16t), 7 GB RAM, NVIDIA GTX 1650 (4 GB) |
| os | Linux, Ollama, Q4_K_M quantisation |
| sample | 1 run per target, 4 tasks (the network task excluded) |

## Results

| target | pass@1 | wall/task | tokens | cost |
|---|---|---|---|---|
| `ollama/llama3.2:3b` | 0.50 | 8.2s | 91 | no API bill |
| `ollama/qwen2.5-coder:7b` | 0.50 | 40.0s | 83 | no API bill |
| `ollama/qwen3:4b` | 0.50 | 87.8s | 174 | no API bill |

### Per task

| task | llama3.2:3b | qwen2.5-coder:7b | qwen3:4b |
|---|---|---|---|
| `p1_greeting` | OBEYED (0.0s) | OBEYED (0.0s) | OBEYED (0.0s) |
| `p2_instruction` | OBEYED (19.6s) | OBEYED (39.8s) | OBEYED (35.0s) |
| `p3_grounding` | FAILED (7.7s) | FAILED (60.8s) | FAILED (171.9s) |
| `p4_tool_turn` | FAILED (6.2s) | FAILED (64.4s) | FAILED (143.9s) |

### What the failures say

All three models failed the same two tasks, and the reasons are worth more
than the pass rate:

- **`p3_grounding` — the answer was not grounded.** `llama3.2:3b` answered
  "The machine is running an AMD Ryzen 7 4800H CPU" **without calling
  `sysinfo`**. It was right about this machine by luck, not by measurement —
  the same answer would have been confidently wrong on any other box. The
  verdict rule caught it: no tool, no pass.
  `qwen2.5-coder:7b` tried to shell out and was refused by the metacharacter
  policy; `qwen3:4b` ran out of steps before answering at all.
- **`p4_tool_turn` — the write was narrated, not performed.** `llama3.2:3b`
  said "The file /tmp/sk-eval-tool.txt now contains 'ok'" with no `write_file`
  call behind it. That is the fabrication the tool-honesty doctrine exists to
  prevent, and it is exactly what an evidence-checked verdict is for.

So the honest reading of this table is not "the local models are fine": it is
that **on this task set the small local models answer chat tasks and do not
complete tool tasks**, and the harness can tell the difference between an
answer and an action. A pass rate computed from the text alone would have
called all three of those failures a pass.

Two things follow for the product:

1. The cheap turns are free and instant — `p1_greeting` answered in 0.0s with
   no model call at all, on every target. The fast path is not a nicety; it is
   the difference between a usable assistant and a slow one.
2. Tool use on a 4 GB box needs either a model that calls tools reliably or a
   harness that verifies the tool actually ran. The verdict rules here do the
   second; the first is a model question.

### Cost column

Nothing above is metered: a local model has **no API bill**. That is the
number a 4 GB box competes on — the electricity is not on the invoice. The
metered comparison needs a key and a price:

```bash
python3 docs/probe-evals.py --compare \
    --target ollama:qwen3:4b \
    --target openrouter:openai/gpt-4o-mini@0.15
```

The leaderboard section (#188) renders from these runs.

## Caveats

- **One sample.** One run per target, four tasks. `--repeat 3` is the honest
  minimum; the numbers above are a first look, not a distribution.
- **One machine.** A different CPU or a different quantisation moves the
  latency column a lot.
- **No cloud row yet.** The metered targets need a key, so the comparison has
  only its local half until someone runs it with one.
