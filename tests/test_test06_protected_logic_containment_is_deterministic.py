"""TEST-06 — the Protected_Logic containment check is deterministic, not clock-dependent.

WHAT THE FINDING WAS
====================
`listing_projection.protected_logic_tokens` keeps `str(scalar)` for every leaf at least
three characters long, and `assert_contains_no_protected_logic` did bare SUBSTRING
containment of each token against every value in a response. The seeded strategy carries
`"ema_zzq_secret_window": 233`, so `"233"` was a token -- and `"233" in text` is true of
the timestamp `1760036233`, the count `2337`, the price `233.90`, and any request id that
happens to contain those three digits.

So whether `test_the_permitted_paths_disclose_no_protected_logic_either` (TEST-06) and
`test_protected_logic_containment.py::test_trace_and_export_surfaces_contain_no_protected_logic`
passed depended on what digits the current wall-clock timestamps contained. Measured
directly: TEST-06 FAILED in the Phase 8 PostgreSQL run `FULLREG_P8PG` and PASSED in
`FULLREG_P8PG2`, with no change to it or to the code it examines. A security assertion
whose verdict depends on the clock is indistinguishable from no assertion.

THE FIX, AND WHAT IT DELIBERATELY IS NOT
========================================
A purely numeric token is now matched on a NUMERIC-VALUE BOUNDARY: the digits must not be
flanked by another digit or a decimal point. `233` still matches the value 233 (in its own
field, as a list element, or as the word "233" in free text) but no longer matches
`1760036233`, `2337`, `233.90` or `0.2335`. Distinctive string tokens (node ids, indicator
names, path segments, hashes) are still matched by substring containment, which is correct
and deterministic for them.

This is NOT:

* exempting short tokens -- `233` is still a protected token and is still searched for;
* imposing a minimum token length -- the length floor is unchanged at 3;
* changing the seed values to avoid the collision.

Each of those would narrow the security assertion. What changed is that a numeric token is
searched for as the NUMBER it is rather than as a character substring, which preserves the
property and removes the clock dependence. The brief requires both halves to be proven:
that the original security property still holds, and that the verdict no longer depends on
the time.
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend_app.backend.marketplace import listing_projection as lp

# The seed value at the heart of TEST-06: the EMA window is 233, a bare integer.
NUMERIC_TOKEN = "233"
DECIMAL_TOKEN = "3.2502"            # atr_zzq_secret_multiplier, a decimal threshold
STRING_TOKEN = "ema_zzq_secret_window"  # a distinctive parameter name

TOKENS = frozenset({NUMERIC_TOKEN, DECIMAL_TOKEN, STRING_TOKEN})


def _leaks(response, tokens=TOKENS) -> bool:
    """True iff the containment oracle reports a leak in `response`."""
    try:
        lp.assert_contains_no_protected_logic(response, tokens, structural_keys=frozenset())
        return False
    except AssertionError:
        return True


# ══════════════════════════════════════════════════════════════════════════
#  1. THE SECURITY PROPERTY STILL HOLDS — a real leak is still caught
# ══════════════════════════════════════════════════════════════════════════


def test_a_numeric_value_leaked_in_its_own_field_is_caught():
    """The parameter returned as its own value is the leak the check exists for."""
    assert _leaks({"window": 233}) is True
    assert _leaks({"window": "233"}) is True


def test_a_numeric_value_leaked_inside_free_text_is_caught():
    """A value embedded in a sentence, bounded by non-digits, is still a leak."""
    assert _leaks({"message": "the window is 233 bars"}) is True
    assert _leaks({"note": "(window=233)"}) is True


def test_a_numeric_value_leaked_as_a_list_element_is_caught():
    assert _leaks({"params": [14, 233, "3.2502"]}) is True


def test_a_decimal_threshold_leaked_is_caught():
    assert _leaks({"multiplier": "3.2502"}) is True
    assert _leaks({"multiplier": 3.2502}) is True


def test_a_distinctive_string_token_is_still_caught_by_substring():
    """The non-numeric path is unchanged: a node/parameter name leaks wherever it appears,
    including embedded in a longer string."""
    assert _leaks({"detail": "field ema_zzq_secret_window was used"}) is True
    assert _leaks({"x": {"y": ["ema_zzq_secret_window"]}}) is True


def test_a_leak_nested_deep_is_still_caught():
    assert _leaks({"a": {"b": {"c": [{"d": 233}]}}}) is True


# ══════════════════════════════════════════════════════════════════════════
#  2. THE CLOCK NO LONGER DECIDES — false positives are gone
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-09T17:23:53Z",      # contains "233" in the seconds+... run
        "1760036233",                 # a unix epoch that literally ends 233
        "2337",                       # 233 as a prefix of a larger number
        "0.2335",                     # 233 inside a decimal
        "233900",                     # 233 as a prefix
        "req-1760036233-abcd",        # a request id built from a timestamp
    ],
)
def test_a_number_merely_containing_the_digits_is_not_a_leak(timestamp):
    """These all contain the characters "233" and NONE of them is the value 233.

    Under the old substring check every one of these was a false positive, and whether a
    response contained such a value depended on the wall clock -- which is the whole of
    TEST-06.
    """
    assert _leaks({"created_at": timestamp}) is False, (
        f"{timestamp!r} was reported as leaking the value 233; it merely contains those "
        f"digits as part of a larger number"
    )


def test_a_response_built_entirely_from_the_current_clock_does_not_leak():
    """The exact failure shape: a response whose only numbers are timestamps.

    `233` must not match any of them regardless of what time it is.
    """
    import time
    now = int(time.time())
    response = {
        "request_id": f"req-{now}",
        "created_at": now,
        "expires_at": now + 2592000,
        "count": 2337,
        "nested": [{"t": now - 233_000}, {"t": now + 233}],
    }
    # `now + 233` is a sum, not the value 233, so it does not leak; `now - 233_000` likewise.
    assert _leaks(response) is False


def test_the_verdict_is_identical_across_a_thousand_random_clocks():
    """DETERMINISM, stated as the property it is.

    The old check's verdict was a function of the response's digits, so a response made of
    random timestamps gave random verdicts. The fixed check must give the SAME verdict --
    "no leak" -- for every one of a thousand random clock values, because none of them is
    the value 233.
    """
    rng = random.Random(20261009)  # fixed seed, so the test itself is reproducible
    verdicts = set()
    for _ in range(1000):
        epoch = rng.randint(1_000_000_000, 2_000_000_000)
        response = {
            "request_id": f"req-{epoch}-{rng.randint(0, 999999)}",
            "created_at": epoch,
            "window_count": rng.randint(1, 100000),
            "price_minor": rng.randint(1, 10_000_000),
        }
        verdicts.add(_leaks(response))

    assert verdicts == {False}, (
        f"the containment verdict varied across random clock values ({verdicts}); it is "
        f"still clock-dependent, which is TEST-06"
    )


def test_a_randomized_clock_still_fails_when_the_real_value_is_present():
    """The negative control for the determinism test: with the ACTUAL value 233 present,
    every random clock must yield 'leak'. Otherwise 'always False' would pass vacuously."""
    rng = random.Random(42)
    verdicts = set()
    for _ in range(200):
        epoch = rng.randint(1_000_000_000, 2_000_000_000)
        response = {
            "created_at": epoch,
            "indicators": {"ema_zzq_secret_window": 233},  # the real leak
        }
        verdicts.add(_leaks(response))

    assert verdicts == {True}, (
        "a response that genuinely carries the value 233 was not always flagged; the "
        "fix has weakened the security property"
    )


# ══════════════════════════════════════════════════════════════════════════
#  3. THE FIX DID NOT TAKE A FORBIDDEN SHAPE
# ══════════════════════════════════════════════════════════════════════════


def test_short_and_numeric_tokens_are_still_in_the_token_set():
    """The brief forbids exempting short tokens or raising the length floor.

    `233` is three characters, which is the existing floor, and it is still produced as a
    token. The fix changed HOW it is matched, not WHETHER it is searched for.
    """
    strategy_row = {"indicators": {"ema_zzq_secret_window": 233}}
    tokens = lp.protected_logic_tokens(strategy_row=strategy_row)
    assert "233" in tokens, (
        "the value 233 is no longer a protected token; the fix exempted it rather than "
        "matching it precisely, which narrows the assertion"
    )
    assert lp.PROTECTED_LOGIC_TOKEN_MIN_LENGTH == 3, (
        "the token length floor changed; the brief forbids raising it as the TEST-06 fix"
    )
