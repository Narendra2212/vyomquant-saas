"""Phase 10 / P-23 draw-order experiment: summarize an in-test measurement log.

Parses the per-run census lines written by staging/run_p23_draw_order_measure.ps1 and
reports, for the four DRAWN buckets, every run's count, the floor, and the minimum margin
across runs - plus a pass/fail tally and a floor-breach check.

Usage: python staging/summarize_p23_measure.py staging/p23_measure_<nonce>.txt [label]
"""

from __future__ import annotations

import pathlib
import re
import statistics
import sys
from typing import Dict, List

DRAWN = (
    "side_buy",
    "side_sell",
    "limit2_was_a_second_limit_order",
    "limit2_was_a_second_market_order",
)

RUN_RE = re.compile(r"\[in-test run (\d+) of (\d+)\].*?([\d.]+)s\s+verdict=(\S+)")
BUCKET_RE = re.compile(r"^\s+(\S+)\s+(\d+)\s+(\d+)\s+([\d.]+)x")


def main() -> int:
    path = pathlib.Path(sys.argv[1])
    label = sys.argv[2] if len(sys.argv) > 2 else path.name
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()

    runs: List[Dict[str, object]] = []
    current: Dict[str, object] | None = None
    floors: Dict[str, int] = {}

    for line in text:
        m = RUN_RE.search(line)
        if m:
            current = {
                "n": int(m.group(1)),
                "seconds": float(m.group(3)),
                "verdict": m.group(4),
                "counts": {},
            }
            runs.append(current)
            continue
        b = BUCKET_RE.match(line)
        if b and current is not None:
            bucket, seen, floor = b.group(1), int(b.group(2)), int(b.group(3))
            if bucket in DRAWN:
                current["counts"][bucket] = seen  # type: ignore[index]
                floors[bucket] = floor

    print(f"=== {label} ===")
    print(f"runs parsed: {len(runs)}")
    verdicts = [r["verdict"] for r in runs]
    passed = sum(1 for v in verdicts if v == "PASSED")
    print(f"PASSED: {passed}/{len(runs)}   other: "
          f"{[v for v in verdicts if v != 'PASSED'] or 'none'}")
    total_s = sum(float(r["seconds"]) for r in runs)
    print(f"duration: total {total_s:.1f}s, mean {total_s / max(len(runs), 1):.1f}s/run")
    print(f"floors: {floors}")

    print(f"\n{'run':>4s} {'side_buy':>9s} {'side_sell':>10s} "
          f"{'l2_limit':>9s} {'l2_market':>10s}  verdict")
    breaches = []
    for r in runs:
        c = r["counts"]  # type: ignore[index]
        cells = []
        for bucket in DRAWN:
            v = c.get(bucket)
            cells.append("   -" if v is None else f"{v:4d}")
            if v is not None and v < floors.get(bucket, 0):
                breaches.append((r["n"], bucket, v, floors[bucket]))
        print(f"{r['n']:>4} {cells[0]:>9s} {cells[1]:>10s} {cells[2]:>9s} "
              f"{cells[3]:>10s}  {r['verdict']}")

    print(f"\n{'bucket':36s} {'floor':>6s} {'min':>5s} {'mean':>7s} {'max':>5s} "
          f"{'min margin':>11s}  breaches")
    for bucket in DRAWN:
        vals = [r["counts"].get(bucket) for r in runs]  # type: ignore[index]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        floor = floors.get(bucket, 0)
        margin = min(vals) / floor if floor else float("inf")
        n = sum(1 for v in vals if v < floor)
        print(f"  {bucket:34s} {floor:6d} {min(vals):5d} {statistics.mean(vals):7.1f} "
              f"{max(vals):5d} {margin:10.2f}x  {n}/{len(vals)}")

    print(f"\nFLOOR BREACHES: {breaches if breaches else 'NONE'}")
    worst = min(
        (min(v for v in (r['counts'].get(b) for r in runs) if v is not None) / floors[b])  # type: ignore[union-attr]
        for b in DRAWN if b in floors
    )
    print(f"WORST MIN MARGIN ACROSS DRAWN BUCKETS: {worst:.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
