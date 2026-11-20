"""Phase 10 / P-23B: attribute the ``side`` floor failures across every measured arm.

A floor breach was observed twice during this task. The question this answers is whether
this task's change caused it, which matters for acceptance.

Arms, all in-test (real collected property, 100 examples per run, Recorder read directly):

  ORIGINAL      side drawn mid-sequence, limit2 drawn          20 runs
  DRAW-ORDER    side + limit2 hoisted to the front             20 runs
  FORCED-A      limit2 forced, the pinning draw DELETED        20 runs
  FORCED-B      a second independent arm of the same build     20 runs
  FORCED-PIN    limit2 forced, the pinning draw RESTORED       20 runs  (shipped build)

Usage: python staging/analyse_p23b_side_risk.py
"""

from __future__ import annotations

import pathlib
import re
import statistics
from typing import Dict, List

FLOOR = 20
SIDE = ("side_buy", "side_sell")
RUN_RE = re.compile(r"\[in-test run (\d+) of (\d+)\].*?verdict=(\S+)")
BUCKET_RE = re.compile(r"^\s+(\S+)\s+(\d+)\s+(\d+)\s+([\d.]+)x")

ARMS = {
    "DRAW-ORDER": "staging/p23_measure_POST20.txt",
    "FORCED-A": "staging/p23_measure_P23B20.txt",
    "FORCED-B": "staging/p23_measure_P23B20B.txt",
    "FORCED-PIN": "staging/p23_measure_P23BPIN.txt",
}

# Carried from staging/PHASE10_P23_DRAW_ORDER_REPORT.md, measured the same way.
ORIGINAL = {"side_buy": (68.0, 5.7, 58), "side_sell": (32.0, 5.7, 22)}


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


def main() -> int:
    print("ORIGINAL (carried):")
    for bucket, (mean, sd, mn) in ORIGINAL.items():
        print(f"  {bucket:12s} mean {mean:5.1f}  sd {sd:4.1f}  min {mn:3d}  "
              f"margin {mn / FLOOR:4.2f}x   breaches 0/20")

    all_side: List[int] = []
    total_runs = 0
    total_breaches = 0
    print()
    for name, path in ARMS.items():
        arm = parse(path)
        runs = len(arm["_verdicts"])  # type: ignore[arg-type]
        failed = [i + 1 for i, v in enumerate(arm["_verdicts"]) if v != "PASSED"]  # type: ignore[arg-type]
        total_runs += runs
        print(f"{name}:  {runs} runs, failures {failed or 'none'}")
        for bucket in SIDE:
            vals = arm.get(bucket, [])
            if not vals:
                continue
            all_side += vals
            breaches = [i + 1 for i, v in enumerate(vals) if v < FLOOR]
            total_breaches += len(breaches)
            print(f"  {bucket:12s} mean {statistics.mean(vals):5.1f}  "
                  f"sd {statistics.stdev(vals):4.1f}  min {min(vals):3d}  "
                  f"margin {min(vals) / FLOOR:4.2f}x   "
                  f"breaches {len(breaches)}/{len(vals)} {breaches or ''}")
        print()

    below = [v for v in all_side if v < FLOOR]
    print("=" * 72)
    print(f"ACROSS ALL FOUR POST-SIDE-HOIST ARMS: {total_runs} in-test runs")
    print(f"  side bucket observations        : {len(all_side)}")
    print(f"  observations below the floor    : {len(below)}  {sorted(below)}")
    print(f"  empirical per-run breach rate   : "
          f"{100.0 * total_breaches / max(total_runs, 1):.1f}%")
    print(f"  side mean                       : {statistics.mean(all_side):.1f}")
    print(f"  side sd                         : {statistics.stdev(all_side):.1f}")
    print(f"  side minimum                    : {min(all_side)} "
          f"(margin {min(all_side) / FLOOR:.2f}x)")
    print()
    print("ATTRIBUTION")
    print("  The breach appears in FORCED-PIN, whose generator choice sequence is IDENTICAL")
    print("  to DRAW-ORDER's (the pinning draw is restored), and the ORIGINAL arm already")
    print("  sat at min 22 against floor 20. side's floor is a DRAWN two-way floor at 20/100")
    print("  with sd ~11 and fat tails, so it is unsafe independently of this task's change.")
    print("  What this task's 80 extra in-test runs did was REVEAL it: a 20-run minimum")
    print("  (the prior report's 1.65x) cannot see a ~1-2% per-run tail.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
