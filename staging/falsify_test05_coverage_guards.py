"""Phase 10 / TEST-05: do the two new coverage guards actually BITE?

A guard that cannot fail is worse than no guard, so each one is driven against a
deliberately broken version of what it guards and must raise AssertionError.

Usage: python staging/falsify_test05_coverage_guards.py
"""

from __future__ import annotations

import sys
from typing import Any, Callable, List, Tuple

sys.path.insert(0, ".")

from tests.property import test_order_idempotence_under_retry as m  # noqa: E402

RESULTS: List[Tuple[str, bool, str]] = []


def expect_failure(name: str, mutate: Callable[[], Any], guard: Callable[[], None]) -> None:
    """Apply a mutation, run the guard, require AssertionError, then restore."""
    restore = mutate()
    try:
        guard()
    except AssertionError as exc:
        RESULTS.append((name, True, str(exc).splitlines()[0][:150]))
        return
    except Exception as exc:  # pragma: no cover - a wrong kind of failure is still a failure
        RESULTS.append((name, False, f"raised {type(exc).__name__} not AssertionError: {exc}"))
        return
    finally:
        restore()
    RESULTS.append((name, False, "GUARD DID NOT FIRE - it is vacuous"))


def expect_pass(name: str, guard: Callable[[], None]) -> None:
    try:
        guard()
    except Exception as exc:  # pragma: no cover
        RESULTS.append((name, False, f"unmutated guard failed: {type(exc).__name__}: {exc}"))
        return
    RESULTS.append((name, True, "passes unmutated"))


# ── baseline: both guards pass as shipped ────────────────────────────────────────────────
expect_pass("existence-floor guard passes as shipped",
            m.test_every_existence_floor_is_forced_rather_than_left_to_the_draw)
expect_pass("legality guard passes as shipped",
            m.test_the_forced_coverage_sequences_are_legal_generator_draws)


# ── 1. the forced coverage is removed entirely (TEST-05's original state) ────────────────
def drop_all_forced() -> Callable[[], None]:
    saved = m.DETERMINISTIC_COVERAGE
    m.DETERMINISTIC_COVERAGE = ()
    def restore() -> None:
        m.DETERMINISTIC_COVERAGE = saved
    return restore


expect_failure("forced coverage removed entirely", drop_all_forced,
               m.test_every_existence_floor_is_forced_rather_than_left_to_the_draw)


# ── 2. only the length-10 case is removed (the exact Phase 9 shortfall) ──────────────────
def drop_len10() -> Callable[[], None]:
    saved = m.DETERMINISTIC_COVERAGE
    m.DETERMINISTIC_COVERAGE = tuple(s for s in saved if (1 + len(s.steps)) != 10)
    def restore() -> None:
        m.DETERMINISTIC_COVERAGE = saved
    return restore


expect_failure("only the length-10 forced case removed", drop_len10,
               m.test_every_existence_floor_is_forced_rather_than_left_to_the_draw)


# ── 3. a NEW existence floor is added with no forced coverage ────────────────────────────
def add_unforced_existence_floor() -> Callable[[], None]:
    saved_floors = dict(m.P12_1_FLOORS)
    saved_existence = m.EXISTENCE_FLOORS
    m.P12_1_FLOORS["sequence_length_7"] = 1
    m.EXISTENCE_FLOORS = tuple(
        sorted(k for k, v in m.P12_1_FLOORS.items() if v <= 1)
    )
    def restore() -> None:
        m.P12_1_FLOORS.clear()
        m.P12_1_FLOORS.update(saved_floors)
        m.EXISTENCE_FLOORS = saved_existence
    return restore


expect_failure("a new existence floor added with no forced case", add_unforced_existence_floor,
               m.test_every_existence_floor_is_forced_rather_than_left_to_the_draw)


# ── 4. Phase.explicit disabled, so @example cases would never run ────────────────────────
def disable_explicit_phase() -> Callable[[], None]:
    from hypothesis import Phase, settings as hsettings
    saved = m.PROPERTY_SETTINGS
    m.PROPERTY_SETTINGS = hsettings(
        parent=saved,
        phases=[p for p in saved.phases if p is not Phase.explicit],
    )
    def restore() -> None:
        m.PROPERTY_SETTINGS = saved
    return restore


expect_failure("Phase.explicit removed from the property settings", disable_explicit_phase,
               m.test_every_existence_floor_is_forced_rather_than_left_to_the_draw)


# ── 5. max_examples raised instead of the case being forced ──────────────────────────────
def raise_max_examples() -> Callable[[], None]:
    from hypothesis import settings as hsettings
    saved = m.PROPERTY_SETTINGS
    m.PROPERTY_SETTINGS = hsettings(parent=saved, max_examples=400)
    def restore() -> None:
        m.PROPERTY_SETTINGS = saved
    return restore


expect_failure("max_examples raised to 400", raise_max_examples,
               m.test_every_existence_floor_is_forced_rather_than_left_to_the_draw)


# ── 6-9. a forced sequence drifts outside what the generator can draw ────────────────────
def _replace_first(**changes: Any) -> Callable[[], Callable[[], None]]:
    def mutate() -> Callable[[], None]:
        saved = m.DETERMINISTIC_COVERAGE
        original = saved[0]
        fields = {
            "key": original.key, "flavour": original.flavour, "side": original.side,
            "quantity": original.quantity, "limit_price": original.limit_price,
            "close": original.close, "fee_rate": original.fee_rate, "steps": original.steps,
        }
        fields.update(changes)
        m.DETERMINISTIC_COVERAGE = (m._RetrySequence(**fields),) + tuple(saved[1:])
        def restore() -> None:
            m.DETERMINISTIC_COVERAGE = saved
        return restore
    return mutate


expect_failure("a forced quantity the generator cannot draw",
               _replace_first(quantity="7.5"),
               m.test_the_forced_coverage_sequences_are_legal_generator_draws)
expect_failure("a forced step kind the generator cannot draw",
               _replace_first(steps=("submit", "retry")),
               m.test_the_forced_coverage_sequences_are_legal_generator_draws)
expect_failure("a forced sequence longer than the generator's ceiling",
               _replace_first(steps=tuple(["retry"] * 12)),
               m.test_the_forced_coverage_sequences_are_legal_generator_draws)
expect_failure("a forced key over the migration's 128-char CHECK",
               _replace_first(key="k" * 200),
               m.test_the_forced_coverage_sequences_are_legal_generator_draws)
expect_failure("a forced key with whitespace the generator filters out",
               _replace_first(key="has space"),
               m.test_the_forced_coverage_sequences_are_legal_generator_draws)
expect_failure("two forced sequences sharing one idempotency key",
               _replace_first(key=m.FORCED_LEN_2.key),
               m.test_the_forced_coverage_sequences_are_legal_generator_draws)


if __name__ == "__main__":
    width = max(len(name) for name, _, _ in RESULTS)
    ok = True
    for name, passed, detail in RESULTS:
        flag = "OK  " if passed else "FAIL"
        ok = ok and passed
        print(f"[{flag}] {name.ljust(width)}  {detail}")
    print(f"\nALL GUARD FALSIFICATIONS BEHAVED AS REQUIRED: {ok}")
    raise SystemExit(0 if ok else 1)
