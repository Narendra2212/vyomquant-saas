"""Unit tests for the presentation and ingestion boundaries of the marketplace money module.

Feature: marketplace-subscriptions-paper-trading (task 4.6).
Module under test: ``backend_app/backend/marketplace/money.py`` (task 4.1).
Requirements: 8.12, 8.13.

These are convenience boundary tests around the two ``Decimal`` conversion helpers
``to_major`` and ``from_major_string``. The 90/10 split's whole input space is already
covered by the property tests P-1 … P-4 in ``tests/property/test_money_split.py``; here we
pin the specific behaviour of the presentation (Minor_Units -> major ``Decimal``) and
ingestion (major decimal *string* -> Minor_Units) boundaries:

* round trips for both supported currencies (USD, INR), each with a two-place minor unit,
* rejection of a ``float`` argument at the ingestion boundary (Requirement 8.13 forbids
  binary floating-point money values reaching the boundary), and
* rejection of a value carrying more decimal places than the currency's exponent admits.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend_app.backend.marketplace.money import (
    InvalidAmount,
    from_major_string,
    to_major,
)


# ──────────────────────────────────────────────────────────────────────────
# to_major / from_major_string round trips (Requirement 8.12)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("currency", ["USD", "INR"])
@pytest.mark.parametrize(
    "amount_minor",
    [0, 1, 99, 100, 101, 12_345, 99_999_999_999],
)
def test_to_major_from_major_string_round_trip(currency: str, amount_minor: int) -> None:
    """Minor_Units -> major ``Decimal`` -> back to the same Minor_Units, for USD and INR."""
    major = to_major(amount_minor, currency)
    assert isinstance(major, Decimal)
    # Quantized to the currency's two-place exponent.
    assert -major.as_tuple().exponent == 2
    assert from_major_string(str(major), currency) == amount_minor


@pytest.mark.parametrize("currency", ["USD", "INR"])
@pytest.mark.parametrize(
    ("text", "expected_minor"),
    [
        ("0.00", 0),
        ("0.01", 1),
        ("1.00", 100),
        ("1.23", 123),
        ("-1.00", -100),
        ("1e3", 100_000),
    ],
)
def test_from_major_string_to_major_round_trip(
    currency: str, text: str, expected_minor: int
) -> None:
    """A decimal string ingests to Minor_Units and renders back to the same magnitude."""
    amount_minor = from_major_string(text, currency)
    assert amount_minor == expected_minor
    # to_major renders the same value quantized to the exponent; ingest it once more to
    # confirm the pair is a stable round trip.
    assert from_major_string(str(to_major(amount_minor, currency)), currency) == expected_minor


# ──────────────────────────────────────────────────────────────────────────
# Rejection of a float argument (Requirement 8.13)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("currency", ["USD", "INR"])
@pytest.mark.parametrize("bad", [1.0, 0.07, -1.0, 100.0])
def test_from_major_string_rejects_float(currency: str, bad: float) -> None:
    """A ``float`` at the ingestion boundary is refused, not coerced.

    A binary floating-point value has already lost exactness by the time it reaches this
    boundary, so the module refuses it rather than laundering the error into the ledger.
    """
    with pytest.raises(InvalidAmount):
        from_major_string(bad, currency)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────
# Rejection of too many decimal places (Requirement 8.12)
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("currency", ["USD", "INR"])
@pytest.mark.parametrize("text", ["1.005", "0.001", "1.234", "-1.999"])
def test_from_major_string_rejects_extra_decimal_places(currency: str, text: str) -> None:
    """A value with more decimal places than the currency's exponent admits is refused.

    Both USD and INR carry a two-place minor unit, so a three-place value is not a
    representable amount and picking a rounding direction here would invent an amount the
    caller did not send.
    """
    with pytest.raises(InvalidAmount):
        from_major_string(text, currency)
