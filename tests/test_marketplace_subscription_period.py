"""Unit tests for ``marketplace/subscription_period.py`` (task 4.7, Requirements 11.4, 11.5).

These pin the named edge cases of the calendar rule - month-end clamping, the leap-year day,
the December-to-January year roll - and the UTC precondition. The whole input space is
covered separately by properties P-12 and P-14 in
``tests/property/test_subscription_period.py``.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend_app.backend.marketplace.subscription_period import (
    add_one_calendar_month,
    ensure_utc,
    period_for_activation,
    period_for_renewal,
)


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class TestAddOneCalendarMonth:
    """Requirement 11.4: same clock time one calendar month later, day clamped."""

    def test_mid_month_keeps_the_day_and_the_clock_time(self):
        assert add_one_calendar_month(_utc(2024, 3, 15, 13, 47, 9, 123456)) == _utc(
            2024, 4, 15, 13, 47, 9, 123456
        )

    def test_january_31_clamps_to_february_29_in_a_leap_year(self):
        assert add_one_calendar_month(_utc(2024, 1, 31, 8, 0, 0)) == _utc(
            2024, 2, 29, 8, 0, 0
        )

    def test_january_31_clamps_to_february_28_in_a_common_year(self):
        assert add_one_calendar_month(_utc(2023, 1, 31, 8, 0, 0)) == _utc(
            2023, 2, 28, 8, 0, 0
        )

    def test_january_31_clamps_to_february_28_in_a_century_non_leap_year(self):
        # 1900 and 2100 are divisible by 4-rule exceptions; calendar.monthrange knows it.
        assert add_one_calendar_month(_utc(2100, 1, 31)) == _utc(2100, 2, 28)

    def test_may_31_clamps_to_june_30(self):
        assert add_one_calendar_month(_utc(2024, 5, 31, 23, 59, 59, 999999)) == _utc(
            2024, 6, 30, 23, 59, 59, 999999
        )

    def test_december_rolls_the_year(self):
        assert add_one_calendar_month(_utc(2024, 12, 31, 0, 0, 0)) == _utc(
            2025, 1, 31, 0, 0, 0
        )

    def test_result_keeps_the_utc_tzinfo_singleton(self):
        assert add_one_calendar_month(_utc(2024, 6, 1)).tzinfo is timezone.utc

    def test_result_is_strictly_later_than_the_input(self):
        t = _utc(2024, 1, 31, 12, 0, 0)
        assert add_one_calendar_month(t) > t

    def test_naive_datetime_is_rejected(self):
        with pytest.raises(ValueError, match="UTC"):
            add_one_calendar_month(datetime(2024, 3, 15, 12, 0, 0))

    def test_non_utc_zone_is_rejected(self):
        with pytest.raises(ValueError, match="UTC"):
            add_one_calendar_month(
                datetime(2024, 3, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
            )

    def test_equivalent_zero_offset_tzinfo_is_rejected_until_normalised(self):
        # What an ISO-8601 "+00:00" string parses to: same instant, different tzinfo object.
        parsed = datetime.fromisoformat("2024-03-15T12:00:00+00:00")
        if parsed.tzinfo is timezone.utc:  # pragma: no cover - CPython returns utc here
            pytest.skip("this interpreter already canonicalises +00:00 to timezone.utc")
        with pytest.raises(ValueError, match="ensure_utc"):
            add_one_calendar_month(parsed)

    def test_non_datetime_is_rejected(self):
        with pytest.raises(TypeError):
            add_one_calendar_month("2024-03-15T12:00:00Z")


class TestEnsureUtc:
    """The sanctioned adapter for an instant that arrived with another tzinfo."""

    def test_utc_datetime_passes_through_unchanged(self):
        t = _utc(2024, 3, 15, 12, 0, 0, 500)
        assert ensure_utc(t) == t
        assert ensure_utc(t).tzinfo is timezone.utc

    def test_equivalent_zero_offset_is_canonicalised_without_moving_the_clock(self):
        t = datetime(2024, 3, 15, 12, 0, 0, tzinfo=timezone(timedelta(0)))
        result = ensure_utc(t)
        assert result.tzinfo is timezone.utc
        assert result == t
        assert (result.year, result.month, result.day, result.hour) == (2024, 3, 15, 12)

    def test_non_zero_offset_is_converted_to_the_same_instant(self):
        t = datetime(2024, 3, 15, 17, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        result = ensure_utc(t)
        assert result == t
        assert result == _utc(2024, 3, 15, 12, 0)

    def test_naive_datetime_is_rejected(self):
        with pytest.raises(ValueError, match="aware"):
            ensure_utc(datetime(2024, 3, 15, 12, 0, 0))

    def test_output_is_accepted_by_add_one_calendar_month(self):
        t = datetime(2024, 1, 31, 8, 0, tzinfo=timezone(timedelta(0)))
        assert add_one_calendar_month(ensure_utc(t)) == _utc(2024, 2, 29, 8, 0)


class TestPeriodForActivation:
    """Requirement 11.4: start is the confirmation instant, expiry one month later."""

    def test_start_is_the_confirmation_instant_and_expiry_is_one_month_later(self):
        confirmation = _utc(2024, 1, 31, 10, 15, 30, 7)
        start, expiry = period_for_activation(confirmation)
        assert start == confirmation
        assert expiry == _utc(2024, 2, 29, 10, 15, 30, 7)

    def test_expiry_is_strictly_greater_than_start(self):
        start, expiry = period_for_activation(_utc(2024, 8, 31, 0, 0, 0))
        assert expiry > start

    def test_naive_confirmation_instant_is_rejected(self):
        with pytest.raises(ValueError, match="confirmation_instant"):
            period_for_activation(datetime(2024, 1, 31, 10, 0, 0))


class TestPeriodForRenewal:
    """Requirement 11.5: one month after the later of current expiry and confirmation."""

    def test_early_renewal_extends_from_the_current_expiry(self):
        current_expiry = _utc(2024, 3, 31, 9, 0, 0)
        confirmation = _utc(2024, 3, 20, 14, 0, 0)  # paid before the period ended
        assert period_for_renewal(current_expiry, confirmation) == _utc(
            2024, 4, 30, 9, 0, 0
        )

    def test_late_renewal_extends_from_the_confirmation_instant(self):
        current_expiry = _utc(2024, 3, 31, 9, 0, 0)
        confirmation = _utc(2024, 4, 10, 14, 30, 0)  # paid after expiry
        assert period_for_renewal(current_expiry, confirmation) == _utc(
            2024, 5, 10, 14, 30, 0
        )

    def test_renewal_at_the_expiry_instant_extends_from_it(self):
        instant = _utc(2024, 3, 31, 9, 0, 0)
        assert period_for_renewal(instant, instant) == _utc(2024, 4, 30, 9, 0, 0)

    def test_new_expiry_is_strictly_greater_than_the_previous_expiry(self):
        current_expiry = _utc(2024, 1, 31, 23, 59, 59, 999999)
        for confirmation in (
            _utc(2024, 1, 1),
            current_expiry,
            _utc(2025, 6, 15, 12, 0, 0),
        ):
            assert period_for_renewal(current_expiry, confirmation) > current_expiry

    def test_repeated_renewal_walks_forward_month_by_month(self):
        confirmation = _utc(2024, 1, 31, 6, 0, 0)
        expiry = period_for_activation(confirmation)[1]
        assert expiry == _utc(2024, 2, 29, 6, 0, 0)
        expiry = period_for_renewal(expiry, confirmation)
        assert expiry == _utc(2024, 3, 29, 6, 0, 0)
        expiry = period_for_renewal(expiry, confirmation)
        assert expiry == _utc(2024, 4, 29, 6, 0, 0)

    def test_naive_current_expiry_is_rejected(self):
        with pytest.raises(ValueError, match="current_expiry"):
            period_for_renewal(datetime(2024, 3, 31, 9, 0, 0), _utc(2024, 4, 1))

    def test_naive_confirmation_instant_is_rejected(self):
        with pytest.raises(ValueError, match="confirmation_instant"):
            period_for_renewal(_utc(2024, 3, 31, 9, 0, 0), datetime(2024, 4, 1))
