"""Phase 10 / TEST-05 probe: WHERE does the "zero length-10s in a whole run" come from?

The census probe showed a bimodal result: most 100-example runs draw length 10 about
14 times, but ~10% of runs draw it ZERO times. Independent per-example draws cannot do
that (with p~=0.14, P(0 in 100) ~= 5e-7), so generation is correlated within a run and
the question is where the correlation enters.

Three configurations, same ``max_examples``:
  A. ``st.integers(2, 10)`` alone.
  B. the composite, but with the length draw ISOLATED (no list, no filtered text).
  C. the real ``submit_retry_restart_sequences()`` composite.

Usage: python staging/probe_test05_integers_bimodality.py [TRIALS]
"""

from __future__ import annotations

import collections
import sys
from typing import Any, Dict, List

sys.path.insert(0, ".")

from hypothesis import HealthCheck, Phase, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

TRIALS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
MAX_EXAMPLES = 100

BASE = dict(
    max_examples=MAX_EXAMPLES,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


def _run(label: str, make_test, note: str = "") -> None:
    zero_trials = 0
    counts_per_trial: List[int] = []
    first_zero_sequence: List[int] = []

    for _ in range(TRIALS):
        drawn: List[int] = []
        make_test(drawn)()
        tens = sum(1 for n in drawn if n == 10)
        counts_per_trial.append(tens)
        if tens == 0:
            zero_trials += 1
            if not first_zero_sequence:
                first_zero_sequence = list(drawn)

    hist = collections.Counter(counts_per_trial)
    print(f"\n--- {label} ---{(' ' + note) if note else ''}")
    print(f"  trials={TRIALS} examples/trial={MAX_EXAMPLES}")
    print(f"  trials with ZERO length-10 draws: {zero_trials}/{TRIALS} "
          f"({100.0 * zero_trials / TRIALS:.1f}%)")
    print(f"  mean length-10 count per trial: "
          f"{sum(counts_per_trial) / max(len(counts_per_trial), 1):.2f}")
    print(f"  per-trial count histogram: {dict(sorted(hist.items()))}")
    if first_zero_sequence:
        print(f"  first zero trial: {len(first_zero_sequence)} draws recorded")
        print(f"    value histogram: "
              f"{dict(sorted(collections.Counter(first_zero_sequence).items()))}")
        print(f"    first 40 in order: {first_zero_sequence[:40]}")


# ── A. the bare integer draw ──────────────────────────────────────────────────────────
def make_a(drawn: List[int]):
    @settings(**BASE)
    @given(n=st.integers(min_value=2, max_value=10))
    def t(n: int) -> None:
        drawn.append(n)
    return t


# ── B. integer draw, generate-phase only (no reuse/shrink/target) ─────────────────────
def make_b(drawn: List[int]):
    @settings(**BASE, phases=[Phase.generate])
    @given(n=st.integers(min_value=2, max_value=10))
    def t(n: int) -> None:
        drawn.append(n)
    return t


# ── C. the real composite ─────────────────────────────────────────────────────────────
def make_c(drawn: List[int]):
    from tests.property.test_order_idempotence_under_retry import (
        submit_retry_restart_sequences,
    )

    @settings(**BASE)
    @given(seq=submit_retry_restart_sequences())
    def t(seq: Any) -> None:
        drawn.append(1 + len(seq.steps))
    return t


# ── D. the composite's length draw, but via sampled_from instead of integers ──────────
def make_d(drawn: List[int]):
    @settings(**BASE)
    @given(n=st.sampled_from(tuple(range(2, 11))))
    def t(n: int) -> None:
        drawn.append(n)
    return t


if __name__ == "__main__":
    _run("A: st.integers(2, 10), default phases", make_a)
    _run("B: st.integers(2, 10), generate phase only", make_b)
    _run("C: submit_retry_restart_sequences()", make_c)
    _run("D: st.sampled_from(range(2, 11))", make_d)
