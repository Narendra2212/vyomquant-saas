"""MKT-09 — the 30-day entitlement, measured against the clock rather than asserted.

WHY THIS FILE EXISTS
====================
The session-four report named this the sharpest remaining unknown in Task 15:

    "The 30-day entitlement expiry was not verified against the clock.
     `subscription_period.py`, `expiry_sweep` and `test_entitlement_expiry_boundary`
     exist and pass, but no measurement was taken. An entitlement that fails to expire
     is a revenue defect that is invisible until it is large, and it is the one T15 item
     where the test passing and the behaviour being right are not the same claim."

Two things were then measured, and this file is the second.

**The data half is unverifiable, and that is the finding.** Read-only against production:
`public.library_subscriptions` holds **0 rows**. No marketplace subscription has ever been
created, so no expiry has ever been exercised against real data. There is no revenue defect
because there is no revenue — and the moment the first subscription exists, this file is what
says the expiry works.

**The behaviour half is verifiable now**, because `resolve` takes `now` as an argument
precisely so the decision is testable without waiting a month:

    async def resolve(caller, listing_id, supabase, now: datetime) -> Entitlement

So this drives the REAL `entitlement_resolver.resolve` across the expiry instant and asserts
the verdict flips. Nothing about the clock is mocked; the instant is handed in.

WHAT MAKES THIS DIFFERENT FROM `test_entitlement_expiry_boundary`
=================================================================
That file is a Hypothesis property over the boundary predicate. This one drives the resolver
end to end against a seeded Subscription, and — the part that matters — asserts the
**sweep-independence** claim directly: a row whose stored `status` still reads `'active'`
because the housekeeping sweep never ran is refused anyway, because
`entitlement_resolver.py:514` consults `period_expiry` before it trusts the label.

That claim is load-bearing and it is made in three docstrings. If it were ever false, every
one of those docstrings would still read as if it were true, and a dead worker would silently
become unlimited free access.

WHAT IS NOT CLAIMED
===================
* **Not** that a real subscription has ever expired correctly. Production has none.
* **Not** that the expiry sweep runs in production. `vyomquant-worker-marketplace_expiry` is
  a registered ECS service, but whether it is processing was not measured, and by design it
  does not need to be for entitlement to be correct — only for the stored label to be fresh.
* **Not** anything about the billing side. Whether a renewal was paid for is
  `settlement_service`'s question, not this one.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from backend_app.backend.marketplace import entitlement_resolver as resolver
from backend_app.backend.marketplace import subscription_period as period

OWNER_ID = "11111111-1111-4111-8111-111111111111"
SUBSCRIBER_ID = "22222222-2222-4222-8222-222222222222"
STRANGER_ID = "33333333-3333-4333-8333-333333333333"
LISTING_ID = "44444444-4444-4444-8444-444444444444"
VERSION_ID = "55555555-5555-4555-8555-555555555555"
STRATEGY_ID = "66666666-6666-4666-8666-666666666666"
SUBSCRIPTION_ID = "77777777-7777-4777-8777-777777777777"

#: The instant the Subscription is activated at. Fixed, so no case can pass or fail on when
#: the suite happens to run.
ACTIVATED_AT = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# PART 1 — the period the platform actually grants
# ══════════════════════════════════════════════════════════════════════════


def test_the_granted_period_is_a_calendar_month_which_the_brief_permits() -> None:
    """Task 15 E asks for 30 days "unless the existing billing architecture defines a
    compatible calendar-month mechanism". It does, and this is it.

    `period_for_activation` is asserted rather than described, because "one month" has three
    plausible meanings (30 days, 31 days, same-day-next-month) and only one of them is what
    the subscriber is sold.
    """
    start, expiry = period.period_for_activation(ACTIVATED_AT)
    assert start == ACTIVATED_AT
    assert expiry == period.add_one_calendar_month(ACTIVATED_AT)
    # June 1 -> July 1: a calendar month, which here happens to be 30 days.
    assert expiry == datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert (expiry - start) == timedelta(days=30)


def test_a_calendar_month_is_not_a_fixed_thirty_days_at_month_boundaries() -> None:
    """The distinction the brief's "unless … compatible" clause turns on.

    A January 31 activation cannot expire on February 31. Pinning what it DOES do is what
    stops a future change to `add_one_calendar_month` from silently shortening or lengthening
    what a subscriber paid for.
    """
    jan31 = datetime(2026, 1, 31, 9, 0, 0, tzinfo=timezone.utc)
    expiry = period.add_one_calendar_month(jan31)
    assert expiry == datetime(2026, 2, 28, 9, 0, 0, tzinfo=timezone.utc)
    assert (expiry - jan31) == timedelta(days=28)

    # And a leap year differs, which is the whole reason this is not arithmetic on 30.
    leap = datetime(2028, 1, 31, 9, 0, 0, tzinfo=timezone.utc)
    assert period.add_one_calendar_month(leap) == datetime(
        2028, 2, 29, 9, 0, 0, tzinfo=timezone.utc
    )


# ══════════════════════════════════════════════════════════════════════════
# PART 2 — the resolver across the expiry instant
# ══════════════════════════════════════════════════════════════════════════


class _Table:
    """A chain-able table double that applies the filters the resolver sends.

    Filters are applied, not ignored. A permissive double would return the seeded Subscription
    for any caller, so a resolver that forgot its `user_id` scope would still pass — which is
    the vacuity the schema-faithful doubles elsewhere in this suite exist to prevent.
    """

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows
        self._filters: List[Any] = []

    def select(self, *_a: Any, **_k: Any) -> "_Table":
        return self

    def eq(self, column: str, value: Any) -> "_Table":
        self._filters.append((column, value))
        return self

    def limit(self, *_a: Any, **_k: Any) -> "_Table":
        return self

    def order(self, *_a: Any, **_k: Any) -> "_Table":
        return self

    def maybe_single(self) -> "_Table":
        return self

    def single(self) -> "_Table":
        return self

    def execute(self) -> Any:
        rows = list(self._rows)
        for column, value in self._filters:
            rows = [r for r in rows if str(r.get(column, "")) == str(value)]
        return type("_Resp", (), {"data": rows, "error": None})()


class _DB:
    """The Listing with its embedded Subscription, as PostgREST returns the shape."""

    def __init__(self, *, status: str, period_expiry: Optional[datetime]) -> None:
        subscription = {
            "id": SUBSCRIPTION_ID,
            "user_id": SUBSCRIBER_ID,
            "status": status,
            "period_expiry": period_expiry.isoformat() if period_expiry else None,
        }
        self.stores: Dict[str, List[Dict[str, Any]]] = {
            "library_strategies": [
                {
                    "id": LISTING_ID,
                    "author_id": OWNER_ID,
                    "source_strategy_id": STRATEGY_ID,
                    "moderation_status": "approved",
                    "is_active": True,
                    "library_subscriptions": [subscription],
                    "marketplace_submissions": [
                        {"listing_id": LISTING_ID, "submission_state": "PUBLISHED"}
                    ],
                }
            ],
            "strategy_versions": [
                {
                    "id": VERSION_ID,
                    "strategy_id": STRATEGY_ID,
                    "version": 1,
                    "is_draft": False,
                    "validation_state": "VALID",
                }
            ],
            "marketplace_submissions": [
                {"listing_id": LISTING_ID, "submission_state": "PUBLISHED"}
            ],
        }

    def table(self, name: str) -> _Table:
        return _Table(self.stores.setdefault(name, []))


def _resolve(db: "_DB", *, caller_id: str, now: datetime) -> Any:
    return asyncio.run(resolver.resolve({"id": caller_id}, LISTING_ID, db, now))


def _entitling(verdict: Any) -> bool:
    return verdict.reason in resolver.ENTITLING_REASONS


@pytest.fixture()
def start_and_expiry():
    return period.period_for_activation(ACTIVATED_AT)


def test_a_subscription_entitles_the_instant_before_it_expires(start_and_expiry) -> None:
    _start, expiry = start_and_expiry
    db = _DB(status="active", period_expiry=expiry)
    verdict = _resolve(db, caller_id=SUBSCRIBER_ID, now=expiry - timedelta(seconds=1))
    assert verdict.reason == resolver.EntitlementReason.SUBSCRIBED
    assert _entitling(verdict) is True


def test_a_subscription_stops_entitling_at_its_expiry_instant(start_and_expiry) -> None:
    """The boundary is AT the instant, not after it.

    `expiry_sweep`'s predicate is `period_expiry <= now` and the resolver's is
    `now >= period_expiry`. They have to agree, or a Subscription exists in a window where one
    of the two thinks it is live and the other does not.
    """
    _start, expiry = start_and_expiry
    db = _DB(status="active", period_expiry=expiry)
    verdict = _resolve(db, caller_id=SUBSCRIBER_ID, now=expiry)
    assert verdict.reason == resolver.EntitlementReason.EXPIRED
    assert _entitling(verdict) is False


def test_a_month_later_it_is_still_refused(start_and_expiry) -> None:
    _start, expiry = start_and_expiry
    db = _DB(status="active", period_expiry=expiry)
    verdict = _resolve(db, caller_id=SUBSCRIBER_ID, now=expiry + timedelta(days=30))
    assert verdict.reason == resolver.EntitlementReason.EXPIRED


def test_expiry_does_not_depend_on_the_sweep_having_run(start_and_expiry) -> None:
    """THE LOAD-BEARING CASE.

    The stored `status` still reads `'active'` — exactly what a dead or mis-scheduled
    `marketplace_expiry` worker leaves behind. The resolver must refuse anyway, because
    `entitlement_resolver.py:514` consults `period_expiry` BEFORE it trusts the label.

    If this were ever false, a stopped worker would silently become unlimited free access, and
    the three docstrings that assert sweep-independence would all still read as if it were
    true. That is why the claim is exercised rather than cited.
    """
    _start, expiry = start_and_expiry
    db = _DB(status="active", period_expiry=expiry)

    stored = db.stores["library_strategies"][0]["library_subscriptions"][0]
    assert stored["status"] == "active", "the premise: the label was never swept"

    verdict = _resolve(db, caller_id=SUBSCRIBER_ID, now=expiry + timedelta(hours=1))
    assert verdict.reason == resolver.EntitlementReason.EXPIRED
    assert _entitling(verdict) is False
    # And the label really is untouched — the resolver is a read, not a repair.
    assert stored["status"] == "active"


def test_a_null_period_is_non_entitling_rather_than_perpetual(start_and_expiry) -> None:
    """A null expiry read as "no end" is the defect this direction guards.

    `test_marketplace_concurrency`'s own notes record the historical shape: "A null expiry was
    read as perpetual access, so one unpriced POST turned a lapsed monthly Subscription into
    permanent free access."
    """
    db = _DB(status="active", period_expiry=None)
    verdict = _resolve(db, caller_id=SUBSCRIBER_ID, now=ACTIVATED_AT)
    assert verdict.reason == resolver.EntitlementReason.EXPIRED
    assert _entitling(verdict) is False


def test_the_owner_is_entitled_without_any_subscription(start_and_expiry) -> None:
    """Expiry is a subscriber concept. A creator does not lose their own strategy."""
    _start, expiry = start_and_expiry
    db = _DB(status="active", period_expiry=expiry)
    verdict = _resolve(db, caller_id=OWNER_ID, now=expiry + timedelta(days=365))
    assert verdict.reason == resolver.EntitlementReason.OWNED
    assert _entitling(verdict) is True


def test_a_stranger_is_not_subscribed_rather_than_expired(start_and_expiry) -> None:
    """The two refusals are distinct codes on the wire (Requirement 7.10).

    Collapsing them would tell someone who never subscribed to go and renew.
    """
    _start, expiry = start_and_expiry
    db = _DB(status="active", period_expiry=expiry)
    verdict = _resolve(db, caller_id=STRANGER_ID, now=expiry - timedelta(days=1))
    assert verdict.reason == resolver.EntitlementReason.NOT_SUBSCRIBED
    assert _entitling(verdict) is False


@pytest.mark.parametrize("lapsed", ["expired", "cancelled", "past_due"])
def test_a_lapsed_status_is_refused_even_inside_the_period(lapsed: str) -> None:
    """The clock is authoritative for EXPIRY; it is not a licence to ignore the status.

    A Subscription cancelled mid-period, or one the provider marked past due, is refused even
    though `now < period_expiry`. The two checks are independent and both have to hold.
    """
    _start, expiry = period.period_for_activation(ACTIVATED_AT)
    db = _DB(status=lapsed, period_expiry=expiry)
    verdict = _resolve(db, caller_id=SUBSCRIBER_ID, now=expiry - timedelta(days=5))
    assert _entitling(verdict) is False


def test_the_resolver_reads_period_expiry_in_its_projection() -> None:
    """The decision cannot be made from a column the read does not ask for.

    A projection that stopped naming `period_expiry` would leave the resolver comparing `now`
    against `None` — which answers EXPIRED for everyone, locking out every paying subscriber.
    This asserts the column is in the embed, so that failure mode is caught by a cheap test
    rather than by a support queue.
    """
    assert "period_expiry" in resolver._SUBSCRIPTION_EMBED_COLUMNS
    assert "status" in resolver._SUBSCRIPTION_EMBED_COLUMNS
