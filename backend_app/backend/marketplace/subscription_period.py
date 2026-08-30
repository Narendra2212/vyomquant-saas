"""
backend_app/backend/marketplace/subscription_period.py - Subscription_Period arithmetic.

Spec: marketplace-subscriptions-paper-trading task 4.7. ``design.md`` ->
"``marketplace/subscription_period.py``". Requirements 11.4, 11.5.

Exposes
-------
add_one_calendar_month(t)                    one calendar month later, same clock time,
                                             day clamped to the target month's last day
period_for_activation(confirmation_instant)  -> (start, expiry)          (Requirement 11.4)
period_for_renewal(current_expiry,
                   confirmation_instant)     -> expiry                   (Requirement 11.5)
ensure_utc(t)                                the sanctioned adapter for a tz-aware instant
                                             that carries some other UTC-equivalent tzinfo

WHY THIS MODULE IS PURE
-----------------------
The layering rule of this design, and the convention ``strategy_dag/schema.py`` and
``order_lifecycle_state.py`` already establish: import pulls only the standard library - no
database handle, no HTTP client, no FastAPI import, no clock read. The period arithmetic is
consulted by ``settlement_service`` when a payment is confirmed, by the expiry worker and by
the property tests of tasks 4.8 and 4.9; every one of those callers must be able to import
it without a connection pool, and the arithmetic must be testable without a database.

No function here reads the current time. The instant a period is computed from always
arrives as an argument - the payment confirmation instant recorded by the
Billing_Integration, or the expiry already stored on the subscription row - which is what
makes P-12 and P-14 decidable and the computation reproducible for an audit.

WHY THE UTC CHECK IS AN IDENTITY CHECK
--------------------------------------
Requirements 11.4 and 11.5 are stated in UTC: the period start is "the payment confirmation
instant in UTC" and the expiry is "the same clock time one calendar month later in UTC". The
clamping rule is only well defined once the calendar the day-of-month is read from is fixed,
so a naive datetime, a local-zone datetime, or a zone with a DST rule is rejected rather
than guessed at. ``timezone.utc`` is a singleton, so ``t.tzinfo is timezone.utc`` is the
cheapest exact statement of "this instant is already expressed on the UTC calendar", and it
also excludes ``ZoneInfo("UTC")``, whose arithmetic is offset-dependent by contract even
where it currently agrees.

A datetime that came back from a driver may carry an equivalent fixed-offset tzinfo instead
(``psycopg2.tz.FixedOffsetTimezone(offset=0)``, or the ``timezone(timedelta(0))`` an ISO-8601
``+00:00`` string parses to). Such a value denotes the same instant on the same calendar, so
:func:`ensure_utc` converts it rather than leaving the caller to hand-roll the conversion;
what it will not do is invent an offset for a naive value.

The check is a raised exception, not a bare ``assert``: ``python -O`` strips ``assert``
statements, and a wrong period silently written to ``library_subscriptions.period_expiry``
is a paid-access defect that no later read can detect.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from typing import Tuple

__all__ = [
    "ensure_utc",
    "add_one_calendar_month",
    "period_for_activation",
    "period_for_renewal",
]

# The one fixed offset that denotes UTC, for the equivalence test in ensure_utc.
_ZERO_OFFSET = timedelta(0)


def ensure_utc(t: datetime) -> datetime:
    """Return ``t`` expressed with ``timezone.utc`` as its ``tzinfo``.

    Accepts any tz-aware datetime whose UTC offset at that instant is exactly zero and
    returns it unchanged except for the tzinfo singleton, so the result satisfies the
    precondition of :func:`add_one_calendar_month` without moving the instant or the clock
    time. A datetime on a non-zero offset is converted with ``astimezone``, which does move
    the clock reading to the UTC one.

    Raises:
        TypeError: ``t`` is not a ``datetime``.
        ValueError: ``t`` is naive - it carries no tzinfo, or its tzinfo returns no offset
            for it - so no offset can be established without guessing one.
    """
    if not isinstance(t, datetime):
        raise TypeError(
            f"a datetime is required, got {type(t).__name__}"
        )
    offset = t.utcoffset()
    if offset is None:
        raise ValueError(
            "a timezone-aware datetime is required; a naive datetime carries no offset "
            "and cannot be placed on the UTC calendar"
        )
    if offset == _ZERO_OFFSET:
        # Same instant, same clock reading, canonical tzinfo.
        return t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def _require_utc(t: datetime, name: str) -> None:
    """Reject anything that is not already a ``timezone.utc`` datetime."""
    if not isinstance(t, datetime):
        raise TypeError(f"{name} must be a datetime, got {type(t).__name__}")
    if t.tzinfo is not timezone.utc:
        raise ValueError(
            f"{name} must be a timezone-aware UTC datetime whose tzinfo is "
            f"datetime.timezone.utc, got tzinfo={t.tzinfo!r}; convert it with "
            f"subscription_period.ensure_utc first"
        )


def add_one_calendar_month(t: datetime) -> datetime:
    """Return the instant one calendar month after ``t`` (Requirement 11.4).

    The result is in the month immediately following ``t``'s month, keeps ``t``'s hour,
    minute, second, microsecond and tzinfo exactly, and takes day-of-month
    ``min(t.day, last_day_of_target_month)`` - so 31 January becomes 28 February, or
    29 February in a leap year, and 31 December becomes 31 January of the next year.

    Args:
        t: a tz-aware UTC instant, ``t.tzinfo is datetime.timezone.utc``.

    Returns:
        The clamped, month-rolled instant.

    Raises:
        TypeError: ``t`` is not a ``datetime``.
        ValueError: ``t`` is not expressed on the UTC calendar.
    """
    _require_utc(t, "t")

    if t.month < 12:
        year, month = t.year, t.month + 1
    else:
        year, month = t.year + 1, 1

    last_day = calendar.monthrange(year, month)[1]
    day = min(t.day, last_day)

    # replace() carries hour, minute, second, microsecond, tzinfo and fold across untouched.
    return t.replace(year=year, month=month, day=day)


def period_for_activation(confirmation_instant: datetime) -> Tuple[datetime, datetime]:
    """Return ``(start, expiry)`` for a Subscription becoming ACTIVE (Requirement 11.4).

    The start is the payment confirmation instant itself; the expiry is the same clock time
    one calendar month later, clamped by :func:`add_one_calendar_month`. ``expiry > start``
    always, which is the invariant the ``library_subscriptions`` CHECK constraint of
    Requirement 11.13 and property P-10 both state.

    Args:
        confirmation_instant: the Billing_Integration payment confirmation instant, UTC.

    Returns:
        The period start and the period expiry.

    Raises:
        TypeError: ``confirmation_instant`` is not a ``datetime``.
        ValueError: ``confirmation_instant`` is not expressed on the UTC calendar.
    """
    _require_utc(confirmation_instant, "confirmation_instant")
    return confirmation_instant, add_one_calendar_month(confirmation_instant)


def period_for_renewal(
    current_expiry: datetime, confirmation_instant: datetime
) -> datetime:
    """Return the new expiry for a renewed Subscription (Requirement 11.5).

    One calendar month after the later of the current expiry and the confirmation instant,
    computed by the same rule as :func:`period_for_activation`. Taking the maximum is what
    makes an early renewal extend the period instead of shortening it, and it is why the new
    expiry is strictly greater than ``current_expiry`` for every admissible input (P-14):
    ``max(...) >= current_expiry`` and ``add_one_calendar_month`` is strictly increasing.

    Args:
        current_expiry: the expiry currently stored on the Subscription, UTC.
        confirmation_instant: the renewal payment confirmation instant, UTC.

    Returns:
        The new period expiry.

    Raises:
        TypeError: either argument is not a ``datetime``.
        ValueError: either argument is not expressed on the UTC calendar.
    """
    _require_utc(current_expiry, "current_expiry")
    _require_utc(confirmation_instant, "confirmation_instant")
    return add_one_calendar_month(max(current_expiry, confirmation_instant))
