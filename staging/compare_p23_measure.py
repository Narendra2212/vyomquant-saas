"""Phase 10 / P-23 draw-order experiment: compare the baseline and post-change arms.

Both arms were measured with the SAME mechanism (the real collected property, 20 independent
in-test executions, Recorder read directly), so the comparison is like for like.

Reports, per drawn bucket: mean, standard deviation, min, the distance of the minimum from
the floor, and how far the mean sits from the fair-draw expectation of 50 that the floors'
stated rationale assumes.

Usage: python staging/compare_p23_measure.py
"""

from __future__ import annotations

import pathlib
import re
import statistics
import sys
from typing import Dict, List

sys.path.insert(0, ".")

DRAWN = (
    "side_buy",
    "side_sell",
    "limit2_was_a_second_limit_order",
    "limit2_was_a_second_market_order",
)
FLOOR = 20
FAIR = 50.0

RUN_RE = re.compile(r"\[in-test run (\d+) of (\d+)\].*?verdict=(\S+)")
BUCKET_RE = re.compile(r"^\s+(\S+)\s+(\d+)\s+(\d+)\s+([\d.]+)x")


def parse(path: str) -> Dict[str, List[int]]:
    out: Dict[str, List[int]] = {k: [] for k in DRAWN}
    verdicts: List[str] = []
    for line in pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = RUN_RE.search(line)
        if m:
            verdicts.append(m.group(3))
            continue
        b = BUCKET_RE.match(line)
        if b and b.group(1) in out:
            out[b.group(1)].append(int(b.group(2)))
    out["_verdicts"] = verdicts  # type: ignore[assignment]
    return out


def main() -> int:
    base = parse("staging/p23_measure_BASE20.txt")
    post = parse("staging/p23_measure_POST20.txt")

    for name, arm in (("BASELINE", base), ("POST-CHANGE", post)):
        v = arm["_verdicts"]  # type: ignore[index]
        print(f"{name}: {len(v)} in-test runs, "
              f"PASSED {sum(1 for x in v if x == 'PASSED')}/{len(v)}")
    print(f"\nfloor for every drawn bucket = {FLOOR}; "
          f"the floors' stated rationale assumes a fair draw of about {FAIR:.0f} of each\n")

    header = (f"{'bucket':34s} {'arm':12s} {'mean':>7s} {'sd':>6s} {'min':>5s} "
              f"{'max':>5s} {'min-floor':>10s} {'min margin':>11s} {'|mean-50|':>10s}")
    print(header)
    print("-" * len(header))
    for bucket in DRAWN:
        for name, arm in (("baseline", base), ("post-change", post)):
            vals = arm[bucket]
            if not vals:
                continue
            mean = statistics.mean(vals)
            sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
            print(f"  {bucket:32s} {name:12s} {mean:7.1f} {sd:6.1f} {min(vals):5d} "
                  f"{max(vals):5d} {min(vals) - FLOOR:10d} "
                  f"{min(vals) / FLOOR:10.2f}x {abs(mean - FAIR):10.1f}")
        print()

    print("=" * len(header))
    print("VERDICT PER DRAWN DIMENSION")
    print("=" * len(header))

    for dim, pair in (
        ("side", ("side_buy", "side_sell")),
        ("limit2_order_type",
         ("limit2_was_a_second_limit_order", "limit2_was_a_second_market_order")),
    ):
        b_min = min(min(base[k]) for k in pair)
        p_min = min(min(post[k]) for k in pair)
        b_bias = max(abs(statistics.mean(base[k]) - FAIR) for k in pair)
        p_bias = max(abs(statistics.mean(post[k]) - FAIR) for k in pair)
        b_sd = max(statistics.stdev(base[k]) for k in pair)
        p_sd = max(statistics.stdev(post[k]) for k in pair)
        print(f"\n{dim}:")
        print(f"  mean bias off fair : {b_bias:5.1f} -> {p_bias:5.1f}  "
              f"({'CORRECTED' if p_bias < b_bias / 2 else 'not corrected'})")
        print(f"  per-run sd (worst) : {b_sd:5.1f} -> {p_sd:5.1f}  "
              f"({'wider' if p_sd > b_sd else 'tighter'})")
        print(f"  worst min margin   : {b_min / FLOOR:5.2f}x -> {p_min / FLOOR:5.2f}x  "
              f"(min count {b_min} -> {p_min}, floor {FLOOR})")
        if p_min / FLOOR >= 1.5:
            print("  VERDICT: FIXED - the minority bucket now clears the floor with margin.")
        elif p_min > b_min:
            print("  VERDICT: STILL MARGINAL - bias corrected, but the minimum barely moved.")
        else:
            print("  VERDICT: STILL MARGINAL - bias corrected, minimum not improved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
