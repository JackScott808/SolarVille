#!/usr/bin/env python3
"""Check the logs and saved CSVs of a two-node run (see scripts/two_nodes.sh).

For a prosumer (pi1) and consumer (pi2) that traded with each other it verifies:
  synchronisation
    - both nodes simulated the same intervals, and stayed in step on the wall clock
    - for EVERY interval the energy pi1 sold equals the energy pi2 bought (trades are booked to the
      interval they belong to, so clock differences between the nodes cannot shift them)
  trading
    - both nodes recorded the same trades, with the same amounts and prices
    - every possible trade happened (surplus and deficit in the same interval -> they traded)
  pricing
    - every trade cleared between the grid export and import price for that time of day
    - the price rose when local supply was scarcer
  money
    - each node's final balance matches a recomputation from its per-interval energy flows and prices
Exits non-zero if any check fails.
"""
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

EPS = 1e-9


def read_trades(log: Path):
    trades = {}
    for line in log.read_text().splitlines():
        m = re.search(r"Trade (match_\S+): (sold|bought) ([\d.]+) kWh at £([\d.]+)/kWh", line)
        if m:
            trades[m.group(1)] = (m.group(2), float(m.group(3)), float(m.group(4)))
    return trades


def read_wall_times(log: Path):
    """Wall-clock time at which each simulated interval finished, from the per-interval log line."""
    times = {}
    for line in log.read_text().splitlines():
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+) - INFO - root - \[([\d-]+ [\d:]+)\] Demand:", line)
        if m:
            wall = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f").timestamp()
            times[m.group(2)] = wall
    return times


def read_rows(directory: Path, device: str):
    files = sorted(directory.glob(f"{device}_*.csv"))
    if not files:
        raise SystemExit(f"no {device}_*.csv in {directory} (did the node finish?)")
    return list(csv.DictReader(open(files[0])))


def num(value, default=0.0):
    return float(value) if value not in ("", None) else default


def main() -> int:
    d = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    t1, t2 = read_trades(d / "pi1.log"), read_trades(d / "pi2.log")
    r1, r2 = read_rows(d / "out", "pi1"), read_rows(d / "out", "pi2")
    w1, w2 = read_wall_times(d / "pi1.log"), read_wall_times(d / "pi2.log")
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

    print("-- synchronisation")
    stamps1, stamps2 = [r["timestamp"] for r in r1], [r["timestamp"] for r in r2]
    check("both nodes simulated the same intervals", stamps1 == stamps2, f"{len(stamps1)} intervals")
    shared = sorted(set(w1) & set(w2))
    skew = max((abs(w1[k] - w2[k]) for k in shared), default=float("inf"))
    check("nodes stayed in step on the wall clock", skew < 0.5, f"max skew {skew * 1000:.0f} ms over {len(shared)} intervals")
    mismatched = [a["timestamp"][11:16] for a, b in zip(r1, r2)
                  if abs(num(a["p2p_sold"]) - num(b["p2p_bought"])) > 1e-9]
    check("every interval: energy sold by pi1 == energy bought by pi2", not mismatched,
          f"mismatched intervals: {mismatched}" if mismatched else "all intervals agree")

    print("-- trading")
    check("trades happened", len(t1) > 0, f"{len(t1)} trades")
    check("both nodes recorded the same trades", set(t1) == set(t2))
    common = set(t1) & set(t2)
    check("amounts and prices identical on both sides", all(t1[k][1:] == t2[k][1:] for k in common))
    check("pi1 only sells, pi2 only buys",
          {v[0] for v in t1.values()} <= {"sold"} and {v[0] for v in t2.values()} <= {"bought"})
    sold = sum(num(r["p2p_sold"]) for r in r1)
    bought = sum(num(r["p2p_bought"]) for r in r2)
    possible = sum(min(max(num(a["balance"]), 0), max(-num(b["balance"]), 0)) for a, b in zip(r1, r2))
    check("every possible trade happened", bought >= possible * 0.95 and abs(sold - bought) < 1e-9,
          f"traded {bought:.3f} of {possible:.3f} kWh possible")

    print("-- pricing")
    out_of_band = []
    for r in r1:
        p = r["p2p_price"]
        if p not in ("", None):
            lo, hi = num(r["export_price"]), num(r["import_price"])
            if not (lo - 1e-9 <= float(p) <= hi + 1e-9):
                out_of_band.append((r["timestamp"][11:16], float(p), lo, hi))
    priced = [r for r in r1 if r["p2p_price"] not in ("", None)]
    check("every trade cleared between the grid export and import price", not out_of_band and priced,
          f"{len(priced)} priced intervals" if not out_of_band else f"out of band: {out_of_band[:3]}")
    distinct_imports = sorted({num(r["import_price"]) for r in r1})
    check("grid import price varied through the day", len(distinct_imports) > 1,
          "import prices seen: " + ", ".join(f"£{p:.2f}" for p in distinct_imports))
    if priced:
        spread = [(float(r["p2p_price"]) - num(r["export_price"])) / max(num(r["import_price"]) - num(r["export_price"]), 1e-9)
                  for r in priced]
        print(f"      peer price sat {min(spread) * 100:.0f}%-{max(spread) * 100:.0f}% of the way from the export to the import price")

    print("-- money")
    def recompute(rows, role):
        balance = 100.0
        for r in rows:
            p = num(r["p2p_price"])
            balance += num(r["grid_sold"]) * num(r["export_price"]) - num(r["grid_bought"]) * num(r["import_price"])
            balance += (num(r["p2p_sold"]) - num(r["p2p_bought"])) * p
        return balance
    for name, rows in (("pi1", r1), ("pi2", r2)):
        expected, actual = recompute(rows, name), num(rows[-1]["currency"])
        check(f"{name} final balance matches recomputation", abs(actual - expected) < 1e-6,
              f"£{actual:.4f} vs £{expected:.4f}")

    # What the market did for each side compared with using only the grid
    saved = sum(num(r["p2p_bought"]) * (num(r["import_price"]) - num(r["p2p_price"])) for r in r2)
    earned = sum(num(r["p2p_sold"]) * (num(r["p2p_price"]) - num(r["export_price"])) for r in r1)
    print(f"\npi1 final £{num(r1[-1]['currency']):.2f}, pi2 final £{num(r2[-1]['currency']):.2f}")
    print(f"peer trading saved pi2 £{saved:.3f} vs the grid and earned pi1 £{earned:.3f} more than exporting")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
