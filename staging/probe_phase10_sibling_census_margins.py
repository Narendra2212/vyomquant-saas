"""Phase 10 / Workstream B, Scope 3: measure the SIBLING censuses' actual margins.

TEST-05's defect was an EXISTENCE floor - a census floor on one particular DRAWN value,
which tree-guided generation does not guarantee within a fixed example budget. This probe
asks whether P-21, P-22 or P-23 carry the same shape, by running each real property and
reading the Recorder it built, then reporting count / floor / margin per bucket.

A bucket is only at risk if BOTH:
  * the case it counts is DRAWN rather than forced into every example, and
  * its margin (count / floor) is small.

A floor that is the arithmetic consequence of forcing a case into every example cannot
fail to a draw, whatever its absolute value.

No sibling test is modified. Read-only.

Usage: python staging/probe_phase10_sibling_census_margins.py [p21|p22|p23|all]
"""

from __future__ import annotations

import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, ".")

from tests.property import paper_census  # noqa: E402

CAPTURED: List[Any] = []
_Real = paper_census.Recorder


class _Capturing(_Real):  # type: ignore[misc, valid-type]
    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        CAPTURED.append(self)


class _StubNode:
    nodeid = "staging/probe_phase10_sibling_census_margins.py::probe"
    hypothesis_statistics: Optional[str] = None


class _StubRequest:
    node = _StubNode()


def run(label: str, module_name: str, test_name: str) -> None:
    import importlib

    mod = importlib.import_module(module_name)
    CAPTURED.clear()
    paper_census.Recorder = _Capturing  # type: ignore[assignment]
    setattr(mod, "Recorder", _Capturing)
    started = time.time()
    try:
        fn = getattr(mod, test_name)
        try:
            fn(_StubRequest())
        except TypeError:
            fn()
        verdict = "PASSED"
    except AssertionError as exc:
        verdict = f"FAILED: {str(exc)[:300]}"
    except Exception as exc:  # noqa: BLE001
        verdict = f"ERROR {type(exc).__name__}: {str(exc)[:300]}"
    finally:
        paper_census.Recorder = _Real  # type: ignore[assignment]
        setattr(mod, "Recorder", _Real)
    elapsed = time.time() - started

    print("\n" + "=" * 100)
    print(f"{label}   module={module_name}   {elapsed:.1f}s   verdict={verdict}")
    print("=" * 100)
    if not CAPTURED:
        print("  no Recorder captured")
        return
    rec = CAPTURED[-1]
    counts: Dict[str, int] = dict(rec.counts)
    floors: Dict[str, int] = dict(rec.floors)
    rows = []
    for key in sorted(floors):
        floor = floors[key]
        seen = counts.get(key, 0)
        margin = (seen / floor) if floor else float("inf")
        rows.append((key, seen, floor, margin))
    print(f"  {'bucket':46s} {'seen':>7s} {'floor':>7s} {'margin':>8s}")
    for key, seen, floor, margin in rows:
        flag = ""
        if margin < 1.0:
            flag = "  <-- SHORT"
        elif margin < 1.3:
            flag = "  <-- THIN"
        print(f"  {key:46s} {seen:7d} {floor:7d} {margin:7.2f}x{flag}")
    tight = [r for r in rows if r[3] < 1.3]
    print(f"\n  buckets with margin < 1.30x: {len(tight)}")
    for key, seen, floor, margin in tight:
        print(f"    {key}: {seen}/{floor} = {margin:.2f}x")
    print(f"  minimum margin over all {len(rows)} buckets: "
          f"{min(r[3] for r in rows):.2f}x")
    print(f"  floors at or below 1 (TEST-05's shape): "
          f"{[k for k, v in floors.items() if v <= 1] or 'NONE'}")


TARGETS = {
    "p21": ("P-21 repeated fill application",
            "tests.property.test_paper_idempotence",
            "test_p21_duplicate_fill_event_changes_nothing"),
    "p22": ("P-22 duplicate order intent",
            "tests.property.test_paper_idempotence",
            "test_p22_duplicate_order_intent_yields_one_order"),
    "p23": ("P-23 concurrent intents vs sequential oracle",
            "tests.property.test_paper_confluence",
            "test_p23_concurrent_intents_match_a_sequential_order"),
}

if __name__ == "__main__":
    which = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    keys = list(TARGETS) if which == "all" else [which]
    for attempt in range(repeats):
        for k in keys:
            label, module_name, test_name = TARGETS[k]
            suffix = f"  [in-test run {attempt + 1} of {repeats}]" if repeats > 1 else ""
            run(label + suffix, module_name, test_name)
