"""Live eval harness (#283): the fixed task set, with speed, quality, and cost.

Not part of pytest: it spends model time and, for cloud tiers, money. Run it
by hand:

    python3 docs/probe-evals.py                     # local fast tier
    python3 docs/probe-evals.py --tier smart        # local smart tier
    python3 docs/probe-evals.py --task p3_grounding # one task
    python3 docs/probe-evals.py --allow-network     # include the network task
    python3 docs/probe-evals.py --json              # machine-readable

Cloud tiers need a key and a spend cap:

    SIDEKICK_API_KEY=sk-or-... python3 docs/probe-evals.py \\
        --provider openrouter --model openai/gpt-4o-mini --price-per-mtok 0.15

The task set lives in ``docs/eval/tasks.json`` and does not change between
runs. What this measures:

- **Speed** — wall time per task, and the time to the first token.
- **Quality** — the verdict per task: OBEYED, PARAPHRASED, or FAILED.
- **Cost** — the estimated tokens and, when a price is given, what they cost.

The output is paste-ready: the three tables and a one-line summary are what
goes into an issue or a release note.

A nightly run is documented in ``docs/eval/README.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

TASKS_PATH = Path(__file__).resolve().parent / "eval" / "tasks.json"
SESSION_PREFIX = "eval"


def load_tasks(path: Path = TASKS_PATH) -> list[dict]:
    """The fixed task set."""
    raw = json.loads(path.read_text())
    if raw.get("schema") != 1:
        raise SystemExit(f"{path}: unsupported schema {raw.get('schema')!r}")
    return list(raw.get("tasks") or [])


def verdict(reply: str, expected: list[str], *, tool_ran: bool = True) -> str:
    """OBEYED / PARAPHRASED / FAILED — the verdict style of probe-routing.py.

    Every expected substring has to be present for OBEYED; some of them for
    PARAPHRASED; none for FAILED. A task that needed a tool and never called
    it is FAILED whatever the answer says: an answer without the grounding is
    a guess, and a guess is not a pass.
    """
    if not tool_ran:
        return "FAILED"
    wanted = [w for w in (expected or []) if str(w).strip()]
    if not wanted:
        return "OBEYED"
    text = str(reply or "").lower()
    hits = sum(1 for want in wanted if str(want).lower() in text)
    if hits == len(wanted):
        return "OBEYED"
    if hits:
        return "PARAPHRASED"
    return "FAILED"


def pass_at_k(n: int, c: int, k: int) -> float:
    """The unbiased pass@k estimator: 1 - C(n-c, k) / C(n, k).

    `n` samples were drawn, `c` of them solved the task, and we ask for the
    probability that a draw of `k` of them contains at least one solution. With
    k = 1 that is the plain pass rate; with k = n it is "was it ever solved".
    """
    if n <= 0 or k <= 0:
        return 0.0
    if c <= 0:
        return 0.0
    if k >= n:
        return 1.0 if c > 0 else 0.0
    from math import comb

    return 1.0 - comb(n - c, k) / comb(n, k)


def _expected_from_tool(kind: str, results: dict[str, str]) -> str:
    """Pull the required substring out of a tool result."""
    if kind == "sysinfo_cpu":
        raw = results.get("sysinfo", "")
        for line in str(raw).splitlines():
            if line.strip().lower().startswith("cpu:"):
                return line.split(":", 1)[1].strip()
        return ""
    return ""


def run_task(task: dict, cfg, *, allow_network: bool) -> dict:
    """Run one task and measure it."""
    from sk.agent import run_agent

    session = f"{SESSION_PREFIX}-{int(time.time())}-{task['id']}"
    tools_used: list[str] = []
    results: dict[str, str] = {}
    first_token = {"at": None}
    started = time.monotonic()

    def on_token(_chunk: str) -> None:
        if first_token["at"] is None:
            first_token["at"] = time.monotonic()

    def on_tool(name: str, _args: dict, result) -> None:
        tools_used.append(name)
        results[name] = str(result)

    try:
        answer = run_agent(
            task["prompt"],
            [],
            cfg,
            on_tool=on_tool,
            on_token=on_token,
            approve=lambda name, args: True,
            session=session,
        )
    except Exception as e:  # a failed turn is a result, not a crash
        answer = f"ERROR: {e}"
    elapsed = time.monotonic() - started

    expected = list(task.get("expect") or [])
    from_tool = task.get("expect_from")
    if from_tool:
        derived = _expected_from_tool(from_tool, results)
        if derived:
            expected.append(derived)
    needed = str(task.get("tool") or "")
    tool_ran = (not needed) or needed in tools_used

    from sk.tokens import estimate_tokens

    tokens = estimate_tokens(task["prompt"] + str(answer))
    return {
        "id": task["id"],
        "kind": task.get("kind", "chat"),
        "verdict": verdict(str(answer), expected, tool_ran=tool_ran),
        "seconds": round(elapsed, 2),
        "ttft": round(first_token["at"] - started, 2) if first_token["at"] else None,
        "tokens": tokens,
        "tools": tools_used,
        "answer": str(answer)[:200],
    }


def _config(args) -> object:
    from sk.config import Config, provider_tier

    cfg = Config.load()
    if args.provider:
        cfg.provider = args.provider
    if args.model:
        cfg.model = args.model
    if args.base_url:
        cfg.base_url = args.base_url
    else:
        tier = provider_tier(cfg.provider).get(args.tier, "")
        if tier:
            cfg.model = tier
    if args.spend_cap:
        cfg.spend_cap_usd = float(args.spend_cap)
    if args.network:
        # The network task needs the model to be allowed to fetch. The
        # allowlist lives in the config, so it is widened here and nowhere
        # else — the harness does not touch the user's stored config.
        cfg.egress_allow = tuple(cfg.egress_allow or ()) + (
            "duckduckgo.com",
            "html.duckduckgo.com",
        )
    return cfg


def _table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    out = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    out.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        out.append("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))
    return "\n".join(out)


def render(results: list[dict], *, price_per_mtok: float = 0.0, k: int = 1) -> str:
    """The paste-ready Speed / Quality / Cost tables."""
    speed = [
        [
            r["id"],
            f"{r['seconds']:.2f}s",
            "-" if r["ttft"] is None else f"{r['ttft']:.2f}s",
        ]
        for r in results
    ]
    quality = [
        [
            r["id"],
            r["verdict"],
            f"{r.get('solved', 1)}/{r.get('runs', 1)}",
            ",".join(r["tools"]) or "-",
        ]
        for r in results
    ]
    cost = [[r["id"], str(r.get("total_tokens", r["tokens"]))] for r in results]
    total_tokens = sum(r.get("total_tokens", r["tokens"]) for r in results)
    if price_per_mtok:
        cost.append(
            ["total", f"{total_tokens} tokens ≈ ${total_tokens / 1e6 * price_per_mtok:.4f}"]
        )

    obeyed = sum(1 for r in results if r["verdict"] == "OBEYED")
    paraphrased = sum(1 for r in results if r["verdict"] == "PARAPHRASED")
    failed = sum(1 for r in results if r["verdict"] == "FAILED")
    total_seconds = sum(r["seconds"] for r in results)
    pass1 = sum(r.get("pass_at_1", 1.0) for r in results) / max(1, len(results))
    passk = sum(r.get("pass_at_k", 1.0) for r in results) / max(1, len(results))

    parts = [
        "Speed",
        _table(speed, ["task", "wall", "ttft"]),
        "",
        "Quality",
        _table(quality, ["task", "verdict", "solved", "tools"]),
        "",
        "Cost",
        _table(cost, ["task", "tokens"]),
        "",
        f"summary: {obeyed} obeyed, {paraphrased} paraphrased, {failed} failed · "
        f"pass@1 {pass1:.2f}"
        + (f" · pass@{k} {passk:.2f}" if k > 1 else "")
        + f" · {total_seconds:.1f}s total · {total_tokens} tokens",
    ]
    return "\n".join(parts)


def parse_target(spec: str) -> dict:
    """``provider:model`` or ``provider:model@price_per_mtok``.

    The price is in USD per million tokens; without one, the target is
    reported as unmetered (which is what a local model is).

    The price is separated by ``@`` rather than a colon because model names
    carry colons themselves: ``ollama:qwen3:4b`` is one model, not a model
    called "qwen3" priced at 4b.
    """
    text = str(spec or "")
    price = 0.0
    if "@" in text:
        text, _, raw_price = text.rpartition("@")
        try:
            price = float(raw_price)
        except ValueError:
            price = 0.0
    provider, _, model = text.partition(":")
    return {"provider": provider, "model": model, "price": price, "spec": spec}


def render_comparison(runs: list[dict]) -> str:
    """The comparison table: local against cloud, with the cost column.

    A local model costs no API bill — it costs electricity, which is not on
    the invoice. A metered one costs what the tokens cost. That difference is
    the point of the table.
    """
    rows = []
    for run in runs:
        results = run["results"]
        if not results:
            continue
        pass1 = sum(r.get("pass_at_1", 1.0) for r in results) / len(results)
        wall = sum(r["seconds"] for r in results) / len(results)
        tokens = sum(r.get("total_tokens", r["tokens"]) for r in results)
        price = run["price"]
        if price:
            cost = f"${tokens / 1e6 * price:.4f}"
        else:
            cost = "no API bill"
        rows.append(
            [
                run["label"],
                f"{pass1:.2f}",
                f"{wall:.1f}s",
                str(tokens),
                cost,
            ]
        )
    return _table(rows, ["target", "pass@1", "wall/task", "tokens", "cost"])


def run_target(target: dict, tasks: list[dict], args) -> list[dict]:
    """Run the whole task set against one target."""
    from sk.config import Config, provider_tier

    cfg = Config.load()
    if target["provider"]:
        cfg.provider = target["provider"]
    if target["model"]:
        cfg.model = target["model"]
    elif args.tier:
        tier = provider_tier(cfg.provider, args.tier, "")
        if tier:
            cfg.model = tier
    if args.base_url:
        cfg.base_url = args.base_url
    if args.spend_cap:
        cfg.spend_cap_usd = float(args.spend_cap)
    if args.network:
        cfg.egress_allow = tuple(cfg.egress_allow or ()) + (
            "duckduckgo.com",
            "html.duckduckgo.com",
        )
    results = []
    for task in tasks:
        runs = [
            run_task(task, cfg, allow_network=args.allow_network)
            for _ in range(max(1, args.repeat))
        ]
        solved = sum(1 for r in runs if r["verdict"] == "OBEYED")
        best = next((r for r in runs if r["verdict"] == "OBEYED"), runs[0])
        entry = dict(best)
        entry["runs"] = len(runs)
        entry["solved"] = solved
        entry["pass_at_1"] = round(solved / len(runs), 3)
        entry["pass_at_k"] = round(pass_at_k(len(runs), solved, max(1, args.k)), 3)
        entry["total_tokens"] = sum(r["tokens"] for r in runs)
        results.append(entry)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", default="fast", choices=("fast", "smart"))
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="", help="override the endpoint")
    parser.add_argument("--task", action="append", default=[], help="run only these tasks")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--network", action="store_true", help="allow the model to fetch")
    parser.add_argument("--price-per-mtok", type=float, default=0.0)
    parser.add_argument("--spend-cap", type=float, default=0.0)
    parser.add_argument("--repeat", type=int, default=1, help="samples per task")
    parser.add_argument("--k", type=int, default=1, help="k for pass@k")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="run every target and print the local-vs-cloud comparison",
    )
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="provider:model[:price_per_mtok] to compare (repeatable)",
    )
    args = parser.parse_args(argv)

    tasks = load_tasks()
    if args.task:
        wanted = set(args.task)
        tasks = [t for t in tasks if t["id"] in wanted]
        if not tasks:
            print(f"no task matches {sorted(wanted)}", file=sys.stderr)
            return 2
    if not args.allow_network:
        tasks = [t for t in tasks if t.get("kind") != "network"]

    if args.compare or args.target:
        targets = [parse_target(spec) for spec in args.target]
        if not targets:
            # The default comparison: the two local tiers, which is the point
            # of the table — the local cost column.
            from sk.config import provider_tier

            for tier in ("fast", "smart"):
                targets.append(
                    {
                        "provider": "ollama",
                        "model": provider_tier("ollama", tier, ""),
                        "price": 0.0,
                        "spec": f"ollama:{tier}",
                    }
                )
        runs = []
        for target in targets:
            label = (
                f"{target['provider']}/{target['model']}" if target["model"] else target["provider"]
            )
            print(f"running {label} ...", file=sys.stderr)
            runs.append(
                {
                    "label": label,
                    "price": target["price"],
                    "results": run_target(target, tasks, args),
                }
            )
        if args.json:
            print(json.dumps(runs, indent=2))
            return 0
        print(render_comparison(runs))
        return (
            0 if all(all(r.get("pass_at_1") == 1.0 for r in run["results"]) for run in runs) else 1
        )

    results = run_target({"provider": "", "model": "", "price": 0.0}, tasks, args)

    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    print(render(results, price_per_mtok=args.price_per_mtok, k=max(1, args.k)))
    return 0 if all(r["pass_at_1"] == 1.0 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
