"""Phase 10 / TEST-05: inspect the REAL test's census, not a replica of it.

``--hypothesis-show-statistics`` printed only a "generate phase" line for this property,
with no separate explicit-phase line, so the statistics output alone does not establish
that the ``@example`` cases contributed to the census. This probe settles it by running
the actual collected test function with a stub pytest node and reading the ``Recorder``
it built.

Two runs:
  1. as shipped                      -> every existence floor must be cleared, and the
                                        forced contribution must be visible.
  2. with DETERMINISTIC_COVERAGE=()  -> the ONLY difference is the forced cases, so any
                                        drop in the existence buckets is theirs.

Usage: python staging/probe_test05_real_census_contents.py
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, ".")

from tests.property import paper_census  # noqa: E402
from tests.property import test_order_idempotence_under_retry as m  # noqa: E402


class _StubNode:
    nodeid = "staging/probe_test05_real_census_contents.py::probe"
    hypothesis_statistics: Optional[str] = None


class _StubRequest:
    node = _StubNode()


CAPTURED: List[paper_census.Recorder] = []
_RealRecorder = paper_census.Recorder


class _CapturingRecorder(_RealRecorder):  # type: ignore[misc, valid-type]
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        CAPTURED.append(self)


def run(label: str) -> Dict[str, int]:
    CAPTURED.clear()
    paper_census.Recorder = _CapturingRecorder  # type: ignore[assignment]
    m.Recorder = _CapturingRecorder  # type: ignore[assignment]
    try:
        m.test_order_idempotence_under_generated_retry_and_restart_sequences(_StubRequest())
        verdict = "PASSED"
    except AssertionError as exc:
        verdict = f"FAILED: {str(exc)[:200]}"
    finally:
        paper_census.Recorder = _RealRecorder  # type: ignore[assignment]
        m.Recorder = _RealRecorder  # type: ignore[assignment]

    counts = dict(CAPTURED[-1].counts) if CAPTURED else {}
    print(f"\n--- {label} ---")
    print(f"  property verdict: {verdict}")
    for key in sorted(m.P12_1_FLOORS):
        floor = m.P12_1_FLOORS[key]
        seen = counts.get(key, 0)
        kind = "EXISTENCE " if key in m.EXISTENCE_FLOORS else "distribution"
        flag = "" if seen >= floor else "   <-- SHORT"
        print(f"  {kind} {key:34s} seen {seen:5d}  floor {floor:4d}{flag}")
    return counts


if __name__ == "__main__":
    print(f"EXISTENCE_FLOORS = {m.EXISTENCE_FLOORS}")
    forced_total: Dict[str, int] = {}
    for seq in m.DETERMINISTIC_COVERAGE:
        for bucket, times in m._census_buckets_for(seq).items():
            forced_total[bucket] = forced_total.get(bucket, 0) + times
    print(f"forced contribution expected from DETERMINISTIC_COVERAGE = {forced_total}")

    shipped = run(f"as shipped ({len(m.DETERMINISTIC_COVERAGE)} forced + 100 generated)")

    # A GENUINE control: the property registers its @example cases from
    # DETERMINISTIC_COVERAGE via m._forcing, so emptying the tuple really does remove them.
    saved = m.DETERMINISTIC_COVERAGE
    m.DETERMINISTIC_COVERAGE = ()
    try:
        unforced = run("control: forced set emptied, 100 generated only")
    finally:
        m.DETERMINISTIC_COVERAGE = saved

    print("\n=== did the forced cases land in the census? ===")
    ok = True
    for bucket in sorted(forced_total):
        delta = shipped.get(bucket, 0) - unforced.get(bucket, 0)
        print(f"  {bucket:34s} shipped {shipped.get(bucket, 0):5d} "
              f"vs reference {unforced.get(bucket, 0):5d}  (delta {delta:+d}, "
              f"forced {forced_total[bucket]:+d})")
    print("\n=== the claim that must hold on EVERY run ===")
    for bucket in m.EXISTENCE_FLOORS:
        seen = shipped.get(bucket, 0)
        forced = forced_total.get(bucket, 0)
        good = forced >= m.P12_1_FLOORS[bucket] and seen >= m.P12_1_FLOORS[bucket]
        ok = ok and good
        print(f"  {bucket:34s} floor {m.P12_1_FLOORS[bucket]}, forced alone supplies {forced}, "
              f"run saw {seen} -> {'GUARANTEED' if good else 'NOT GUARANTEED'}")
    print(f"\nEVERY EXISTENCE FLOOR IS SATISFIED BY THE FORCED SET ALONE: {ok}")
    raise SystemExit(0 if ok else 1)
