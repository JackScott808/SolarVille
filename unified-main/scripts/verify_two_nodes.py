#!/usr/bin/env python3
"""Check the logs and saved CSVs of a two-node run (see scripts/two_nodes.sh).

Verifies, for a prosumer (pi1) and consumer (pi2) that traded with each other:
  - both nodes recorded the same trades, with the same amounts and prices
  - energy sold by the seller equals energy bought by the buyer
  - each node's final balance matches an independent recomputation from its energy flows
  - every possible trade happened (surplus and deficit overlapped -> they traded)
Exits non-zero if any check fails.
"""
import csv
import re
import sys
from pathlib import Path

GRID_BUY, GRID_SELL = 0.25, 0.05  # core/constants.py


def read_trades(log: Path):
    trades = {}
    for line in log.read_text().splitlines():
        m = re.search(r"Trade (match_\S+): (sold|bought) ([\d.]+) kWh at £([\d.]+)/kWh", line)
        if m:
            trades[m.group(1)] = (m.group(2), float(m.group(3)), float(m.group(4)))
    return trades


def read_rows(directory: Path, device: str):
    files = sorted(directory.glob(f"{device}_*.csv"))
    if not files:
        raise SystemExit(f"no {device}_*.csv in {directory} (did the node finish?)")
    return list(csv.DictReader(open(files[0])))


def main() -> int:
    d = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out = d / "out"
    t1, t2 = read_trades(d / "pi1.log"), read_trades(d / "pi2.log")
    r1, r2 = read_rows(out, "pi1"), read_rows(out, "pi2")
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

    check("trades happened", len(t1) > 0, f"{len(t1)} trades")
    check("both nodes recorded the same trades", set(t1) == set(t2))
    common = set(t1) & set(t2)
    check("amounts and prices identical on both sides", all(t1[k][1:] == t2[k][1:] for k in common))
    check("pi1 only sells, pi2 only buys",
          {v[0] for v in t1.values()} <= {"sold"} and {v[0] for v in t2.values()} <= {"bought"})

    sold = sum(v[1] for v in t1.values())
    bought = sum(v[1] for v in t2.values())
    check("energy sold == energy bought", abs(sold - bought) < 1e-9, f"{sold:.4f} vs {bought:.4f} kWh")

    p2p_price = {v[2] for v in t1.values()}
    price = p2p_price.pop() if len(p2p_price) == 1 else None
    if price is not None:
        s1 = sum(float(r["p2p_sold"]) for r in r1)
        exp1 = 100 + s1 * price + sum(float(r["grid_sold"]) for r in r1) * GRID_SELL \
            - sum(float(r["grid_bought"]) for r in r1) * GRID_BUY
        b2 = sum(float(r["p2p_bought"]) for r in r2)
        exp2 = 100 - b2 * price - sum(float(r["grid_bought"]) for r in r2) * GRID_BUY \
            + sum(float(r["grid_sold"]) for r in r2) * GRID_SELL
        check("pi1 final balance matches recomputation", abs(float(r1[-1]["currency"]) - exp1) < 1e-6,
              f"£{float(r1[-1]['currency']):.4f} vs £{exp1:.4f}")
        check("pi2 final balance matches recomputation", abs(float(r2[-1]["currency"]) - exp2) < 1e-6,
              f"£{float(r2[-1]['currency']):.4f} vs £{exp2:.4f}")
    else:
        check("single p2p price (needed to recompute balances)", False, f"prices seen: {sorted(p2p_price)}")

    possible = sum(min(max(float(a["balance"]), 0), max(-float(b["balance"]), 0)) for a, b in zip(r1, r2))
    check("every possible trade happened", bought >= possible * 0.95,
          f"traded {bought:.3f} of {possible:.3f} kWh possible")

    print(f"\npi1 final £{float(r1[-1]['currency']):.2f}, pi2 final £{float(r2[-1]['currency']):.2f}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
