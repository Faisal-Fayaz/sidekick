"""Offline token-budget probe (#499): measure every surface's fixed prompt cost.

No model, no key, no network. Run from the repo root:

    uv run python docs/probe-perf.py
    uv run python docs/probe-perf.py --json

It exits non-zero on a budget breach, so CI can gate on it. The live latency
numbers belong to `docs/probe-evals.py` and stay machine-dependent.
"""

from __future__ import annotations

import argparse
import json
import sys

from sk.perf import COMPONENT_BUDGETS, check, measure_all


def render(rows: list[dict]) -> str:
    head = f"{'surface':<14} {'system':>7} {'outside':>8} {'tools':>7} {'fixed':>7} {'history':>8} {'total':>7} {'budget':>7}"
    lines = [head, "-" * len(head)]
    for row in rows:
        lines.append(
            f"{row['surface']:<14} {row['system']:>7} {row['outside']:>8} {row['tools']:>7} "
            f"{row['fixed']:>7} {row['history']:>8} {row['total']:>7} {row['budget']:>7}"
        )
    lines.append("")
    lines.append(
        "component budgets: "
        + ", ".join(f"{key}={value}" for key, value in COMPONENT_BUDGETS.items())
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    rows = measure_all()
    problems = check(rows)
    if args.json:
        print(json.dumps({"rows": rows, "breaches": problems}, indent=2))
    else:
        print(render(rows))
        if problems:
            print("\nBUDGET BREACHES")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print("\nAll surfaces within budget.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
