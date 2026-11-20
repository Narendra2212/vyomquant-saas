"""Phase 10 / P-23B: combine every in-test measurement arm into one comparison.

Four arms, all measured with the same mechanism (the real collected property, 100 examples
per run, Recorder read directly):

  ORIGINAL     side and limit2_order_type drawn in their original positions
  DRAW-ORDER   both hoisted to the front of the generator (prior task)
  FORCED-A     limit2_order_type forced per example, side still drawn first (this task)
  FORCED-B     a second, independent 20-run arm of the same build

Usage: python staging/compare_p23b_arms.py
"""

from __future__ import annotations

import pathlib
import re
import statistics
from typing import Dict, List

FLOOR = 20
PAIRS = {
    "side": ("side_buy", "side_sell"),
    "limit2_order_type": (
        "limit2_was_a_second_limit_order",
        "limit2_was_a_second_market_order",
    ),
}
RUN_RE = re.compile(r"\[in-test run (\d+) of (\d+)\].*?verdict=(\S+)")
BUCKET_RE = re.compile(r"^\s+(\S+)\s+(\d+)\s+(\d+)\s+([\d.]+)x")

# ORIGINAL is not re-measurable without reverting the generator, so its figures are carried
# from staging/PHASE10_P23_DRAW_ORDER_REPORT.md where they were measured the same way.
ORIGINAL = {
    "side_buy": dict(mean=68.0, sd=5.7, min=58, max=78, runs=20),
    "side_sell": dict(mean=32.0, sd=5.7, min=22, max=42, runs=20),
    "limit2_was_a_second_limit_order": dict(mean=65.8, sd=6.9, min=55, max=78, runs=20),
    "limit2_was_a_second_market_order": dict(mean=34.2, sd=6.9, min=22, max=45, runs=20),
}


def parse(path: str) -> Dict[str, List[int]]:
    out: Dict[str, List[int]] = {}
    verdicts: List[str] = []
    for line in pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = RUN_RE.search(line)
        if m:
            verdicts.append(m.group(3))
            continue
        b = BUCKET_RE.match(line)
        if b:
            out.setdefault(b.group(1), []).append(int(b.group(2)))
    out["_verdicts"] = verdicts  # type: ignore[assignment]
    return out


def stats(vals: List[int]) -> Dict[str, float]:
    return dict(
        mean=statistics.mean(vals),
        sd=statistics.stdev(vals) if len(vals) > 1 else 0.0,
        min=min(vals),
        max=max(vals),
        runs=len(vals),
    )


def main() -> int:
    arms = {
        "DRAW-ORDER": parse("staging/p23_measure_POST20.txt"),
        "FORCED-A": parse("staging/p23_measure_P23B20.txt"),
        "FORCED-B": parse("staging/p23_measure_P23B20B.txt"),
    }
    for name, arm in arms.items():
        v = arm["_verdicts"]  # type: ignore[index]
        print(f"{name:12s} {len(v)} in-test runs, PASSED "
              f"{sum(1 for x in v if x == 'PASSED')}/{len(v)}")
    forced_all = {
        k: arms["FORCED-A"].get(k, []) + arms["FORCED-B"].get(k, [])
        for pair in PAIRS.values() for k in pair
    }
    print(f"{'FORCED (A+B)':12s} 40 in-test runs combined")
    print(f"\nfloor on every bucket below = {FLOOR}\n")

    hdr = (f"{'bucket':36s} {'arm':14s} {'runs':>5s} {'mean':>7s} {'sd':>6s} "
           f"{'min':>5s} {'max':>5s} {'min margin':>11s}")
    print(hdr)
    print("-" * len(hdr))
    for _dim, pair in PAIRS.items():
        for bucket in pair:
            rows = [("ORIGINAL", ORIGINAL[bucket])]
            for name in ("DRAW-ORDER", "FORCED-A", "FORCED-B"):
                vals = arms[name].get(bucket, [])
                if vals:
                    rows.append((name, stats(vals)))
            if forced_all.get(bucket):
                rows.append(("FORCED (A+B)", stats(forced_all[bucket])))
            for name, s in rows:
                print(f"  {bucket:34s} {name:14s} {int(s['runs']):5d} {s['mean']:7.1f} "
                      f"{s['sd']:6.1f} {int(s['min']):5d} {int(s['max']):5d} "
                      f"{s['min'] / FLOOR:10.2f}x")
            print()

    print("=" * len(hdr))
    print("VERDICT")
    print("=" * len(hdr))
    for dim, pair in PAIRS.items():
        o_min = min(ORIGINAL[k]["min"] for k in pair)
        d_min = min(min(arms["DRAW-ORDER"][k]) for k in pair)
        f_min = min(min(forced_all[k]) for k in pair)
        f_sd = max(stats(forced_all[k])["sd"] for k in pair)
        print(f"\n{dim}:")
        print(f"  worst min margin  ORIGINAL {o_min / FLOOR:.2f}x"
              f"  ->  DRAW-ORDER {d_min / FLOOR:.2f}x"
              f"  ->  FORCED(40 runs) {f_min / FLOOR:.2f}x")
        print(f"  per-run sd now    {f_sd:.1f}")
        if f_sd == 0.0:
            print("  VERDICT: DETERMINISTIC - identical in every run, no draw involved.")
        elif f_min / FLOOR >= 1.5:
            print("  VERDICT: adequate margin, still drawn.")
        else:
            print("  VERDICT: STILL MARGINAL - drawn, and the minimum stays near the floor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
