"""Phase 10 / Workstream B, Scope 3: measure the SIBLINGS' DRAWN census buckets.

The full-property probe (probe_phase10_sibling_census_margins.py) showed that almost every
P-21/P-22/P-23 floor sits at exactly 1.00x because it is the arithmetic consequence of a case
being FORCED into every example - those cannot fail to a draw. Only a handful of buckets are
decided by a draw, and for those the margin is the whole question.

One run is not an answer, so this probe drives only the GENERATORS (cheap - no simulator, no
persistence) for N independent 100-example runs and reports, per drawn bucket, the minimum
observed count against the floor the module asserts.

Read-only. No sibling test is modified.

Usage: python staging/probe_phase10_sibling_drawn_buckets.py [TRIALS]
"""

from __future__ import annotations

import collections
import statistics
import sys
from typing import Any, Dict, List

sys.path.insert(0, ".")

from hypothesis import HealthCheck, given, settings  # noqa: E402

TRIALS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
MAX_EXAMPLES = 100

BASE = dict(
    max_examples=MAX_EXAMPLES,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


def measure(label: str, strategy, extract, floors: Dict[str, int]) -> None:
    per_trial: Dict[str, List[int]] = {k: [] for k in floors}
    shortfalls: Dict[str, int] = collections.Counter()

    for _ in range(TRIALS):
        counts: Dict[str, int] = collections.Counter()

        @settings(**BASE)
        @given(case=strategy)
        def collect(case: Any) -> None:
            for bucket, times in extract(case).items():
                counts[bucket] += times

        collect()
        for bucket, floor in floors.items():
            value = counts.get(bucket, 0)
            per_trial[bucket].append(value)
            if value < floor:
                shortfalls[bucket] += 1

    print(f"\n=== {label}  ({TRIALS} independent runs of {MAX_EXAMPLES} examples) ===")
    print(f"  {'drawn bucket':44s} {'floor':>6s} {'min':>6s} {'mean':>7s} "
          f"{'max':>6s} {'min margin':>11s}  shortfalls")
    for bucket, floor in floors.items():
        vals = per_trial[bucket]
        margin = min(vals) / floor if floor else float("inf")
        n = shortfalls.get(bucket, 0)
        flag = "  <-- SHORT" if n else ("  <-- THIN" if margin < 1.5 else "")
        print(f"  {bucket:44s} {floor:6d} {min(vals):6d} "
              f"{statistics.mean(vals):7.1f} {max(vals):6d} {margin:10.2f}x  "
              f"{n}/{TRIALS}{flag}")


# ── P-22: side is DRAWN, but marked once per flavour and `market` uses other_side() ──────
def p22() -> None:
    from tests.property.test_paper_idempotence import (
        EXAMPLES,
        SUBMIT_FLAVOURS,
        _first_intent,
        repeated_order_intents,
    )

    def extract(case: Any) -> Dict[str, int]:
        out: Dict[str, int] = collections.Counter()
        for flavour in SUBMIT_FLAVOURS:
            side = str(_first_intent(case, flavour)["side"]).lower()
            out[f"side_{side}"] += 1
        return out

    measure("P-22 drawn buckets", repeated_order_intents(), extract,
            {"side_buy": EXAMPLES, "side_sell": EXAMPLES})


# ── P-21: the repetition buckets are drawn counts bucketed into four bands ───────────────
def p21() -> None:
    from tests.property.test_paper_idempotence import (
        EXAMPLES,
        fill_events_and_repetition_counts,
    )

    def extract(case: Any) -> Dict[str, int]:
        out: Dict[str, int] = collections.Counter()
        for times in case.repetitions:
            if times == 1:
                out["repetition_1"] += 1
            elif times == 2:
                out["repetition_2"] += 1
            elif times <= 4:
                out["repetition_three_or_four"] += 1
            else:
                out["repetition_five_or_more"] += 1
        return out

    measure("P-21 drawn buckets (repetition bands)", fill_events_and_repetition_counts(),
            extract, {
                "repetition_1": EXAMPLES * 4,
                "repetition_2": EXAMPLES * 4,
                "repetition_three_or_four": EXAMPLES * 4,
                "repetition_five_or_more": EXAMPLES * 4,
            })


# ── P-23: side and limit2_order_type are the four DRAWN two-way choices ──────────────────
def p23() -> None:
    from tests.property.test_paper_confluence import EXAMPLES, confluent_intent_sets

    def extract(case: Any) -> Dict[str, int]:
        out: Dict[str, int] = collections.Counter()
        out[f"side_{case.side}"] += 1
        out["limit2_was_a_second_limit_order"
            if case.limit2_order_type == "limit"
            else "limit2_was_a_second_market_order"] += 1
        return out

    measure("P-23 drawn buckets", confluent_intent_sets(), extract, {
        "side_buy": EXAMPLES // 5,
        "side_sell": EXAMPLES // 5,
        "limit2_was_a_second_limit_order": EXAMPLES // 5,
        "limit2_was_a_second_market_order": EXAMPLES // 5,
    })


if __name__ == "__main__":
    p21()
    p22()
    p23()
    print("\nNOTE: these are GENERATOR measurements. The forced buckets (margin exactly "
          "1.00x in the full-property probe) are not listed because no draw can reduce "
          "them - they are the arithmetic consequence of a case being driven on every "
          "example.")
