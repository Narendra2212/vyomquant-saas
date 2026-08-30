"""backend_app/backend/marketplace/money.py - exact money arithmetic in Minor_Units.

Spec: marketplace-subscriptions-paper-trading task 4.1. ``design.md`` -> "``marketplace/money.py``".
Requirements 8.12, 8.13, 9.2, 10.1, 10.2, 10.3.

Exposes
-------
MINOR_UNIT_EXPONENT        ISO 4217 minor-unit exponent per supported currency (Req 8.12)
SUPPORTED_CURRENCIES       the currency codes this module will convert
OWNER_SHARE_PERCENT        90 (Requirement 10.1)
MAX_AMOUNT_MINOR           99,999,999,999 - the top of Requirement 10.1's stated domain
InvalidAmount              raised for an amount outside the admissible domain
UnsupportedCurrency        raised for a currency with no persisted minor-unit exponent
split_ninety_ten(...)      the 90/10 split (Requirements 10.1, 10.2, 10.3)
amount_for_listing(...)    the Listing's ``price_minor``, unchanged (Requirement 8.12)
minor_unit_exponent(...)   the exponent lookup, for callers that format elsewhere
to_major(...)              Minor_Units -> ``Decimal`` major units, presentation boundary only
from_major_string(...)     a decimal *string* -> Minor_Units, ingestion boundary only

WHY THIS MODULE IS PURE
-----------------------
Same convention ``order_lifecycle_state.py`` and ``strategy_dag/schema.py`` already establish:
module import pulls only the standard library - no FastAPI, no database handle, no HTTP client,
no I/O. The split is consulted by the checkout handler, by ``settlement_service``, by the
webhook path and by the earnings reports, and each of those must be able to import the
arithmetic without dragging a connection pool behind it. It also means the whole input space
of Requirement 10.1 is property-testable with no fixture at all (P-1 … P-4, P-7).

WHY THERE IS NO ``float`` IN THIS FILE
--------------------------------------
Requirements 8.13, 9.2 and 10.3 forbid binary floating-point money arithmetic outright. This
module is where that prohibition is made structural rather than aspirational:

* The split is ``int`` end to end. ``(amount_minor * 90) // 100`` on Python ``int`` is exact at
  every magnitude - there is no precision ceiling to overflow and no representation error to
  accumulate - so no rounding policy has to be chosen or documented for it.
* ``float`` is rejected on the way *in* rather than tolerated and coerced. A ``float`` argument
  means the caller has already done inexact arithmetic somewhere upstream, and silently
  accepting it would launder that error into the ledger. ``bool`` is rejected for the same
  reason at a different angle: ``isinstance(True, int)`` is ``True`` in Python, so ``True``
  would otherwise split as one Minor_Unit.
* The two conversions that must produce a non-integer go through ``Decimal``, and only at the
  presentation (``to_major``) and ingestion (``from_major_string``) boundaries. Nothing in
  between ever holds a fractional money value.

WHY VALIDATION HAPPENS BEFORE THE DIVISION
------------------------------------------
``//`` floors, and flooring equals truncation toward zero only for non-negative operands.
Requirement 10.1 specifies "integer division that truncates toward zero" over the inclusive
range 0 … 99,999,999,999, so within that domain ``//`` *is* the specified operation. Rather
than rely on the two agreeing outside the domain, a negative or over-maximum amount is refused
before the division ever runs (P-7). That keeps one operator in the code and one meaning for it.

WHY CONSERVATION IS STRUCTURAL
------------------------------
``platform_fee`` is defined as the residual ``amount_minor - owner_share``, never as a second
percentage computation. Requirement 10.2's ``owner_share + platform_fee == amount`` therefore
holds by construction for every admissible input, with no rounding remainder to account for -
the property test P-1 confirms the code says what this paragraph says, it does not prop it up.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* Persisting a Settlement_Record, and the audit write beside it. Requirement 10.4's write needs
  a database handle, which is exactly what this module must not have. ``settlement_service``
  (task 19) owns the write and delegates only the arithmetic here.
* The ``MarketplaceError`` code catalogue and its FastAPI exception handler (task 5.5).
  ``InvalidAmount`` is a plain module-level exception so that this module stays importable on
  its own; the service layer is where a refused amount becomes an HTTP status.
* A currency-conversion or FX rate of any kind. Requirement 10.7 forbids combining
  Settlement_Records of different currencies into one total, so no cross-currency arithmetic
  exists to support.
* A fallback from ``price`` (the legacy ``NUMERIC(10,2)`` column) when ``price_minor`` is absent.
  That column is mirrored *from* ``price_minor`` by trigger, never the reverse; reading it back
  as the authoritative amount would reintroduce the very inexactness ``price_minor`` was added
  to remove, so :func:`amount_for_listing` refuses instead (Requirement 8.12).
"""

from __future__ import annotations

from decimal import Decimal, DecimalException, InvalidOperation, localcontext
from types import MappingProxyType
from typing import Any, Mapping, Tuple

# ══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════

#: ISO 4217 minor-unit exponent per supported currency (Requirement 8.12). Read-only, because
#: a caller mutating the exponent for a currency would silently change the meaning of every
#: stored amount in that currency.
MINOR_UNIT_EXPONENT: Mapping[str, int] = MappingProxyType({"USD": 2, "INR": 2})

#: The currencies this module will convert. A currency absent here has no persisted exponent,
#: so its Minor_Units have no defined meaning and conversion is refused rather than guessed.
SUPPORTED_CURRENCIES = frozenset(MINOR_UNIT_EXPONENT)

#: The owner's share of a payment, in whole percent (Requirement 10.1).
OWNER_SHARE_PERCENT = 90

#: The top of Requirement 10.1's inclusive domain, 0 … 99,999,999,999 Minor_Units.
MAX_AMOUNT_MINOR = 99_999_999_999

#: Working precision for the two ``Decimal`` boundary conversions. ``MAX_AMOUNT_MINOR`` has 11
#: digits and the largest supported exponent is 2, so 13 significant digits would do; 28 is the
#: ``Decimal`` default and leaves the boundary conversions exact with room to spare, while being
#: set explicitly so a caller's ambient context cannot lower it underneath us.
_CONVERSION_PRECISION = 28


class MoneyError(Exception):
    """Base class for the two refusals this module makes.

    Callers that want to map either refusal onto one HTTP status can catch this; callers that
    need to distinguish "the amount is wrong" from "the currency is unknown" catch the
    subclasses.
    """


class InvalidAmount(MoneyError):
    """An amount is not an admissible integer number of Minor_Units.

    Raised for a non-``int`` (including a ``bool``, a ``float`` and a ``Decimal``), for a
    negative amount, for an amount above :data:`MAX_AMOUNT_MINOR`, and - at the ingestion
    boundary - for a major-unit string carrying more decimal places than the currency's
    minor-unit exponent admits.
    """


class UnsupportedCurrency(MoneyError):
    """No minor-unit exponent is persisted for the requested currency code."""


# ══════════════════════════════════════════════════════════════════════════
# THE 90/10 SPLIT (Requirements 10.1, 10.2, 10.3)
# ══════════════════════════════════════════════════════════════════════════


def split_ninety_ten(amount_minor: int) -> Tuple[int, int]:
    """Split ``amount_minor`` into ``(owner_share, platform_fee)``.

    ``owner_share = (amount_minor * 90) // 100`` - integer division, which truncates toward
    zero across the whole admissible domain because that domain is non-negative
    (Requirement 10.1). ``platform_fee`` is the residual, so
    ``owner_share + platform_fee == amount_minor`` exactly, with no unaccounted remainder
    (Requirement 10.2). Every operand is a Python ``int``; no binary floating point is
    involved at any step (Requirement 10.3).

    The rounding remainder never exceeds one Minor_Unit, and it always falls to the platform:
    ``owner_share`` is the largest whole Minor_Unit count not exceeding 90 percent of the
    amount, so ``owner_share * 100 <= amount_minor * 90 < (owner_share + 1) * 100``.

    Args:
        amount_minor: The payment amount, an ``int`` number of Minor_Units in the inclusive
            range 0 … :data:`MAX_AMOUNT_MINOR`.

    Returns:
        ``(owner_share, platform_fee)``, both ``int`` Minor_Units, both non-negative, both at
        most ``amount_minor``.

    Raises:
        InvalidAmount: If ``amount_minor`` is not an ``int``, is a ``bool``, is negative, or
            exceeds :data:`MAX_AMOUNT_MINOR`. Both checks run **before** the division, so an
            inadmissible amount never reaches the arithmetic.
    """
    _assert_admissible_amount(amount_minor)

    owner_share = (amount_minor * OWNER_SHARE_PERCENT) // 100
    platform_fee = amount_minor - owner_share
    return owner_share, platform_fee


def amount_for_listing(listing_row: Any) -> int:
    """Return the Listing's ``price_minor``, unchanged.

    The Listing carries ``price_minor BIGINT`` and its ISO 4217 ``currency``; that integer is
    the charged amount (Requirements 8.12, 9.2). This function exists so that the checkout
    path has one place to read it from and so that reading it cannot quietly become a
    computation - there is no multiplication, no conversion and no ``float`` here, and the
    legacy ``price NUMERIC(10,2)`` column is never consulted as a fallback.

    Args:
        listing_row: The Listing row, either a mapping (as ``supabase-py`` returns) or any
            object exposing ``price_minor`` as an attribute.

    Returns:
        The ``int`` ``price_minor`` value, exactly as stored.

    Raises:
        InvalidAmount: If the row carries no ``price_minor``, carries ``NULL`` for it, or
            carries a value that is not an admissible integer number of Minor_Units.
    """
    if isinstance(listing_row, Mapping):
        if "price_minor" not in listing_row:
            raise InvalidAmount("listing row carries no price_minor")
        price_minor = listing_row["price_minor"]
    else:
        try:
            price_minor = getattr(listing_row, "price_minor")
        except AttributeError as exc:
            raise InvalidAmount("listing row carries no price_minor") from exc

    if price_minor is None:
        raise InvalidAmount("listing price_minor is NULL")

    _assert_admissible_amount(price_minor, what="listing price_minor")
    return price_minor


# ══════════════════════════════════════════════════════════════════════════
# THE PRESENTATION AND INGESTION BOUNDARIES (Requirements 8.12, 8.13)
# ══════════════════════════════════════════════════════════════════════════


def minor_unit_exponent(currency: Any) -> int:
    """Return the ISO 4217 minor-unit exponent for ``currency``.

    Args:
        currency: The currency code, e.g. ``'USD'``. Matched case-sensitively against
            :data:`MINOR_UNIT_EXPONENT`, because ISO 4217 codes are upper case and accepting
            ``'usd'`` here would make the persisted code ambiguous.

    Returns:
        The exponent, e.g. ``2``.

    Raises:
        UnsupportedCurrency: If no exponent is persisted for that code.
    """
    if not isinstance(currency, str) or currency not in MINOR_UNIT_EXPONENT:
        raise UnsupportedCurrency(
            f"no minor-unit exponent for currency {currency!r}; "
            f"supported: {sorted(SUPPORTED_CURRENCIES)}"
        )
    return MINOR_UNIT_EXPONENT[currency]


def to_major(amount_minor: int, currency: Any) -> Decimal:
    """Convert Minor_Units to a major-unit ``Decimal``, for presentation only.

    This is a boundary function. Nothing downstream of it may feed its result back into an
    arithmetic path - the stored, summed and compared amount is always the Minor_Units integer
    (Requirement 8.12). The result is quantized to the currency's exponent, so ``100`` USD
    Minor_Units renders as ``Decimal('1.00')`` rather than ``Decimal('1')``.

    A signed amount is admitted here because a reported total may be net of reversal
    Settlement_Records (Requirement 10.7); the magnitude is still bounded by
    :data:`MAX_AMOUNT_MINOR`.

    Args:
        amount_minor: An ``int`` number of Minor_Units in the inclusive range
            ``-MAX_AMOUNT_MINOR`` … :data:`MAX_AMOUNT_MINOR`.
        currency: The ISO 4217 currency code the amount is denominated in.

    Returns:
        The amount in major units as an exact ``Decimal``, quantized to the currency's
        minor-unit exponent.

    Raises:
        InvalidAmount: If ``amount_minor`` is not an ``int``, is a ``bool``, or has magnitude
            above :data:`MAX_AMOUNT_MINOR`.
        UnsupportedCurrency: If no exponent is persisted for ``currency``.
    """
    exponent = minor_unit_exponent(currency)
    _assert_integral(amount_minor)
    if abs(amount_minor) > MAX_AMOUNT_MINOR:
        raise InvalidAmount(
            f"amount out of range: {amount_minor} exceeds "
            f"+/-{MAX_AMOUNT_MINOR} Minor_Units in magnitude"
        )

    with localcontext() as ctx:
        ctx.prec = _CONVERSION_PRECISION
        return Decimal(amount_minor).scaleb(-exponent).quantize(_quantum(exponent))


def from_major_string(text: str, currency: Any) -> int:
    """Convert a major-unit decimal *string* to Minor_Units, for ingestion only.

    The argument is a ``str`` and not a number on purpose. A ``float`` reaching this point
    would already have lost the exactness the caller is asking to preserve - ``0.07`` is not
    seven hundredths in binary - so it is refused rather than rounded (Requirement 8.13).
    Parsing goes through ``Decimal``, which reads the decimal string exactly.

    A value carrying more decimal places than the currency's exponent admits is refused rather
    than rounded: ``'1.005'`` in USD is not a representable amount, and picking a rounding
    direction for it here would be inventing an amount the caller did not send.

    Args:
        text: The amount in major units, e.g. ``'1.00'``, ``'-1.00'`` or ``'1e3'``. Surrounding
            whitespace is trimmed; ``NaN`` and the infinities are refused.
        currency: The ISO 4217 currency code the amount is denominated in.

    Returns:
        The equivalent ``int`` number of Minor_Units.

    Raises:
        InvalidAmount: If ``text`` is not a ``str``, is not a finite decimal number, carries
            more decimal places than the currency admits, or has magnitude above
            :data:`MAX_AMOUNT_MINOR` Minor_Units.
        UnsupportedCurrency: If no exponent is persisted for ``currency``.
    """
    exponent = minor_unit_exponent(currency)

    if not isinstance(text, str):
        raise InvalidAmount(
            "major amount must be a decimal string, not "
            f"{type(text).__name__} - a binary floating-point value has already lost "
            "exactness by the time it reaches this boundary"
        )

    with localcontext() as ctx:
        ctx.prec = _CONVERSION_PRECISION
        try:
            major = Decimal(text.strip())
        except InvalidOperation as exc:
            raise InvalidAmount(f"not a decimal number: {text!r}") from exc

        if not major.is_finite():
            raise InvalidAmount(f"not a finite decimal number: {text!r}")

        decimal_places = -major.as_tuple().exponent
        if decimal_places > exponent:
            raise InvalidAmount(
                f"{text!r} carries {decimal_places} decimal places; "
                f"{currency} admits at most {exponent}"
            )

        try:
            scaled = major.scaleb(exponent)
        except DecimalException as exc:
            # An adjusted exponent past the context's range, e.g. '1e999999999'. Out of range
            # for a Minor_Units amount by many orders of magnitude, so it is refused for the
            # same reason an over-maximum integer is.
            raise InvalidAmount(f"amount out of range: {text!r}") from exc

        if scaled != scaled.to_integral_value():
            raise InvalidAmount(f"{text!r} is not a whole number of {currency} Minor_Units")

        amount_minor = int(scaled)

    if abs(amount_minor) > MAX_AMOUNT_MINOR:
        raise InvalidAmount(
            f"amount out of range: {text!r} exceeds "
            f"+/-{MAX_AMOUNT_MINOR} Minor_Units in magnitude"
        )
    return amount_minor


# ══════════════════════════════════════════════════════════════════════════
# INTERNALS
# ══════════════════════════════════════════════════════════════════════════


def _assert_integral(value: Any, what: str = "amount") -> None:
    """Refuse anything that is not a true Python ``int``.

    ``bool`` is excluded explicitly: ``isinstance(True, int)`` is ``True``, so without this
    check ``True`` would be accepted and split as one Minor_Unit. ``float`` and ``Decimal``
    are excluded by the same ``isinstance`` test - a caller holding either has done its
    arithmetic somewhere this module cannot vouch for (Requirement 10.3).
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidAmount(
            f"{what} must be an integer number of Minor_Units, got "
            f"{type(value).__name__}: {value!r}"
        )


def _assert_admissible_amount(value: Any, what: str = "amount") -> None:
    """Refuse anything outside Requirement 10.1's inclusive 0 … ``MAX_AMOUNT_MINOR`` domain."""
    _assert_integral(value, what=what)
    if value < 0 or value > MAX_AMOUNT_MINOR:
        raise InvalidAmount(
            f"{what} out of range: {value} is not within 0..{MAX_AMOUNT_MINOR} Minor_Units"
        )


def _quantum(exponent: int) -> Decimal:
    """The ``Decimal`` quantum for a minor-unit exponent, e.g. ``2`` -> ``Decimal('0.01')``."""
    return Decimal(1).scaleb(-exponent)


__all__ = [
    "MINOR_UNIT_EXPONENT",
    "SUPPORTED_CURRENCIES",
    "OWNER_SHARE_PERCENT",
    "MAX_AMOUNT_MINOR",
    "MoneyError",
    "InvalidAmount",
    "UnsupportedCurrency",
    "split_ninety_ten",
    "amount_for_listing",
    "minor_unit_exponent",
    "to_major",
    "from_major_string",
]
