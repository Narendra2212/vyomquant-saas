"""Phase 10 / TEST-05 probe: do the ``@example`` cases actually RUN and get COUNTED?

The fix's whole weight rests on that. If Hypothesis did not execute the explicit
examples, or executed them without reaching ``recorder.mark``, the targeted test passing
in isolation would be luck (the measured flake rate is ~8%, so a clean run proves
nothing on its own).

Driven with ``max_examples=1`` so the GENERATED contribution is one example and cannot
account for three distinct boundary lengths. The property body is replaced by a counter
only -- the real body is exercised by the test itself, and is not what is in question.

Usage: python staging/probe_test05_forced_examples_run.py
"""

from __future__ import annotations

import collections
import sys
from typing import Any, Dict, List

sys.path.insert(0, ".")

from hypothesis import HealthCheck, example, given, settings  # noqa: E402

from tests.property.test_order_idempotence_under_retry import (  # noqa: E402
    _RetrySequence,
    submit_retry_restart_sequences,
)

FORCED_LEN_10 = _RetrySequence(
    key="forced-len10-" + "r" * 10, flavour="market", side="buy", quantity="1",
    limit_price="100", close="100", fee_rate="0.001",
    steps=("restart", "retry", "restart", "retry", "restart", "retry", "restart", "retry",
           "restart"),
)
FORCED_LEN_5 = _RetrySequence(
    key="forced-len5-restart", flavour="limit", side="sell", quantity="2",
    limit_price="120.50", close="100", fee_rate="0",
    steps=("retry", "restart", "retry", "restart"),
)
FORCED_LEN_2 = _RetrySequence(
    key="forced-len2-retry", flavour="limit", side="buy", quantity="0.25",
    limit_price="50", close="50", fee_rate="0.0025",
    steps=("retry",),
)

seen_keys: List[str] = []
seen_lengths: Dict[int, int] = collections.Counter()


@settings(
    max_examples=1,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@example(seq=FORCED_LEN_10)
@example(seq=FORCED_LEN_5)
@example(seq=FORCED_LEN_2)
@given(seq=submit_retry_restart_sequences())
def check(seq: Any) -> None:
    seen_keys.append(seq.key)
    seen_lengths[1 + len(seq.steps)] += 1


if __name__ == "__main__":
    check()
    print(f"total invocations: {len(seen_keys)}")
    print(f"lengths seen: {dict(sorted(seen_lengths.items()))}")
    forced = [FORCED_LEN_2.key, FORCED_LEN_5.key, FORCED_LEN_10.key]
    for key in forced:
        status = "RAN" if key in seen_keys else "DID NOT RUN"
        print(f"  forced {key!r}: {status}")
    print(f"invocation order (first 8 keys): {seen_keys[:8]}")

    ok = all(key in seen_keys for key in forced)
    print(f"\nALL THREE FORCED EXAMPLES EXECUTED: {ok}")
    for length in (2, 5, 10):
        print(f"  length {length:2d} covered: {seen_lengths.get(length, 0) >= 1}")
    raise SystemExit(0 if ok else 1)
