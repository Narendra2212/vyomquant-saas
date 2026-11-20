"""Phase 10 / TEST-05 probe: measure the GENERATOR's own coverage distribution.

Question this probe answers, and nothing else: over repeated independent runs of
``max_examples=100``, how often does ``submit_retry_restart_sequences()`` fail to
produce each case ``P12_1_FLOORS`` requires? That is the quantity TEST-05's census
asserts, so measuring it settles whether the census is guaranteed by the generator or
is a bet on a draw.

The property BODY is not run here (it is slow and is not what is in question); only the
generator is driven, and the same census buckets are counted. Run with
``--loaded`` to additionally import a large slice of the first-party codebase BEFORE
generating, which is the condition Phase 6-9 attributed the full-suite-only failure to
(Hypothesis 6.x harvests constants out of ``sys.modules``; see
``.venv/Lib/site-packages/hypothesis/internal/conjecture/providers.py``
``_get_local_constants`` / ``_maybe_draw_constant``).

Usage:
    python staging/probe_test05_census_distribution.py [TRIALS] [--loaded]
"""

from __future__ import annotations

import collections
import importlib
import pkgutil
import sys
from typing import Any, Dict, List

sys.path.insert(0, ".")


def _load_a_large_module_set() -> int:
    """Import a wide slice of the first-party tree, to mimic a full-suite process."""
    import backend_app

    loaded = 0
    for info in pkgutil.walk_packages(backend_app.__path__, prefix="backend_app."):
        try:
            importlib.import_module(info.name)
            loaded += 1
        except BaseException:
            pass
    return loaded


def main() -> int:
    args = [a for a in sys.argv[1:]]
    loaded_mode = "--loaded" in args
    positional = [a for a in args if not a.startswith("--")]
    trials = int(positional[0]) if positional else 20

    if loaded_mode:
        count = _load_a_large_module_set()
        print(f"loaded_mode: imported {count} backend_app modules; "
              f"len(sys.modules)={len(sys.modules)}")
    else:
        print(f"minimal_mode: len(sys.modules)={len(sys.modules)}")

    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st  # noqa: F401  (parity of imports)

    from tests.property.test_order_idempotence_under_retry import (
        EXAMPLES,
        MAX_SEQUENCE_LENGTH,
        MIN_SEQUENCE_LENGTH,
        P12_1_FLOORS,
        submit_retry_restart_sequences,
    )

    print(f"EXAMPLES={EXAMPLES} floors={P12_1_FLOORS}")

    length_histogram: Dict[int, int] = collections.Counter()
    shortfall_trials: Dict[str, int] = collections.Counter()
    per_trial: List[Dict[str, int]] = []

    for trial in range(trials):
        counts: Dict[str, int] = collections.Counter()

        @settings(
            max_examples=EXAMPLES,
            deadline=None,
            database=None,
            derandomize=False,
            suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
        )
        @given(seq=submit_retry_restart_sequences())
        def collect(seq: Any) -> None:
            counts["examples"] += 1
            counts[f"flavour_{seq.flavour}"] += 1
            length = 1 + len(seq.steps)
            length_histogram[length] += 1
            if length == MIN_SEQUENCE_LENGTH:
                counts["sequence_length_2"] += 1
            if length >= 5:
                counts["sequence_length_at_or_above_5"] += 1
            if length == MAX_SEQUENCE_LENGTH:
                counts["sequence_length_10"] += 1
            for step in seq.steps:
                counts[f"step_{step}"] += 1

        collect()

        trial_counts = {key: counts.get(key, 0) for key in P12_1_FLOORS}
        per_trial.append(trial_counts)
        for key, floor in P12_1_FLOORS.items():
            # final_state_* is decided by the property body, not the generator.
            if key.startswith("final_state_"):
                continue
            if trial_counts[key] < floor:
                shortfall_trials[key] += 1

        flagged = [
            f"{k}={trial_counts[k]}<{P12_1_FLOORS[k]}"
            for k in P12_1_FLOORS
            if not k.startswith("final_state_") and trial_counts[k] < P12_1_FLOORS[k]
        ]
        print(
            f"trial {trial:3d}: len10={trial_counts['sequence_length_10']:3d} "
            f">=5={trial_counts['sequence_length_at_or_above_5']:3d} "
            f"len2={trial_counts['sequence_length_2']:3d} "
            f"limit={trial_counts['flavour_limit']:3d} "
            f"market={trial_counts['flavour_market']:3d} "
            f"retry={trial_counts['step_retry']:3d} "
            f"restart={trial_counts['step_restart']:3d}"
            + (f"   SHORTFALL {flagged}" if flagged else "")
        )

    print("\n=== length histogram over all trials ===")
    total_lengths = sum(length_histogram.values())
    for length in range(MIN_SEQUENCE_LENGTH, MAX_SEQUENCE_LENGTH + 1):
        n = length_histogram.get(length, 0)
        print(f"  length {length:2d}: {n:6d}  ({100.0 * n / max(total_lengths, 1):5.2f}%)")

    print("\n=== trials with a shortfall, per bucket (generator-decided buckets only) ===")
    any_shortfall = 0
    for trial_counts in per_trial:
        if any(
            trial_counts[k] < P12_1_FLOORS[k]
            for k in P12_1_FLOORS
            if not k.startswith("final_state_")
        ):
            any_shortfall += 1
    for key in sorted(P12_1_FLOORS):
        if key.startswith("final_state_"):
            continue
        n = shortfall_trials.get(key, 0)
        print(
            f"  {key:34s} floor {P12_1_FLOORS[key]:4d}  shortfall in "
            f"{n}/{trials} trials ({100.0 * n / trials:5.1f}%)"
        )
    print(
        f"\nTRIALS WITH ANY SHORTFALL: {any_shortfall}/{trials} "
        f"({100.0 * any_shortfall / trials:5.1f}%)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
