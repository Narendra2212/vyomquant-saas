"""Phase 10 / TEST-05 probe: the census floors are bets on PREFIX VISITATION, and
raising ``max_examples`` does not buy them.

Probe 2 showed that in a 100-example run of the real composite, the drawn lengths
arrive in long runs of one value and only ~7 of the 9 possible lengths are visited at
all. That is the signature of Hypothesis's DataTree-guided generation: it draws a novel
PREFIX and then explores many children under it, varying the LATER draws (the 9-element
step list, the filtered text key) while the EARLY draws (``flavour``, ``length``) stay
put. So ``length`` is not redrawn 100 independent times -- it is visited a handful of
times, deeply.

This probe quantifies what follows from that, for every census bucket the GENERATOR
decides, and across a sweep of ``max_examples`` -- because "just raise max_examples" is
the fix that must be ruled out on evidence rather than asserted against.

Usage: python staging/probe_test05_prefix_autocorrelation.py [TRIALS]
"""

from __future__ import annotations

import collections
import statistics
import sys
from typing import Any, Dict, List

sys.path.insert(0, ".")

from hypothesis import HealthCheck, given, settings  # noqa: E402

from tests.property.test_order_idempotence_under_retry import (  # noqa: E402
    MAX_SEQUENCE_LENGTH,
    MIN_SEQUENCE_LENGTH,
    P12_1_FLOORS,
    submit_retry_restart_sequences,
)

TRIALS = int(sys.argv[1]) if len(sys.argv) > 1 else 60

GENERATOR_BUCKETS = [
    k for k in P12_1_FLOORS if not k.startswith("final_state_") and k != "examples"
]


def sweep(max_examples: int, trials: int) -> Dict[str, Any]:
    shortfalls: Dict[str, int] = collections.Counter()
    distinct_lengths: List[int] = []
    bucket_values: Dict[str, List[int]] = {k: [] for k in GENERATOR_BUCKETS}
    any_shortfall = 0

    for _ in range(trials):
        counts: Dict[str, int] = collections.Counter()
        seen_lengths = set()

        @settings(
            max_examples=max_examples,
            deadline=None,
            database=None,
            suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
        )
        @given(seq=submit_retry_restart_sequences())
        def collect(seq: Any) -> None:
            counts[f"flavour_{seq.flavour}"] += 1
            length = 1 + len(seq.steps)
            seen_lengths.add(length)
            if length == MIN_SEQUENCE_LENGTH:
                counts["sequence_length_2"] += 1
            if length >= 5:
                counts["sequence_length_at_or_above_5"] += 1
            if length == MAX_SEQUENCE_LENGTH:
                counts["sequence_length_10"] += 1
            for step in seq.steps:
                counts[f"step_{step}"] += 1

        collect()
        distinct_lengths.append(len(seen_lengths))

        hit = False
        for key in GENERATOR_BUCKETS:
            value = counts.get(key, 0)
            bucket_values[key].append(value)
            # floors are written against EXAMPLES=100; scale them so the sweep compares
            # like with like rather than rewarding a bigger budget for free.
            floor = P12_1_FLOORS[key]
            scaled = floor if floor <= 1 else int(floor * max_examples / 100)
            if value < scaled:
                shortfalls[key] += 1
                hit = True
        if hit:
            any_shortfall += 1

    return {
        "max_examples": max_examples,
        "shortfalls": dict(shortfalls),
        "any": any_shortfall,
        "distinct_lengths_mean": statistics.mean(distinct_lengths),
        "distinct_lengths_min": min(distinct_lengths),
        "bucket_values": bucket_values,
    }


if __name__ == "__main__":
    print(f"floors (written for max_examples=100): {P12_1_FLOORS}")
    print(f"generator-decided buckets: {GENERATOR_BUCKETS}\n")

    for max_examples in (100, 200, 400):
        trials = TRIALS if max_examples == 100 else max(TRIALS // 2, 10)
        r = sweep(max_examples, trials)
        print(f"=== max_examples={max_examples}, trials={trials} ===")
        print(f"  distinct lengths visited per run: mean "
              f"{r['distinct_lengths_mean']:.2f} of 9, min {r['distinct_lengths_min']}")
        for key in GENERATOR_BUCKETS:
            vals = r["bucket_values"][key]
            floor = P12_1_FLOORS[key]
            scaled = floor if floor <= 1 else int(floor * max_examples / 100)
            n = r["shortfalls"].get(key, 0)
            print(
                f"  {key:34s} floor {scaled:4d}  min {min(vals):4d} "
                f"median {int(statistics.median(vals)):4d} max {max(vals):4d}  "
                f"SHORTFALL {n}/{trials} ({100.0 * n / trials:5.1f}%)"
            )
        print(f"  ANY shortfall: {r['any']}/{trials} "
              f"({100.0 * r['any'] / trials:5.1f}%)\n")
