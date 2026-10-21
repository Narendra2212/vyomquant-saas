"""
tests/test_mkt03_settlement_resumes_across_redeliveries.py

MKT-03: "A settlement retried after a partial failure can leave money recorded as settled while
the buyer's subscription stays unpaid."

``settlement_service._settle_once``'s own existing resumption (``_Attempt``, tracked in-memory)
already covers a transient failure WITHIN one ``settle()`` call's own retry loop -
``test_a_transient_failure_is_resumed_rather_than_restarted`` in test_settlement_service.py proves
it. The gap this file closes is the one that survives a call: when an ENTIRE ``settle()``
invocation exhausts its own retry budget (or the process dies) after the ledger row (step 2) was
written but before the transition (step 3) and the entitlement (step 4), a later, completely
fresh call for the SAME ``provider_reference`` - a webhook redelivery, with no memory of the first
call's ``_Attempt`` - used to find the ledger row via ``uq_settlement_reference_reversal``, read
that alone as "already settled", and return ``DUPLICATE_IGNORED`` without ever checking whether
the Subscription was actually activated. The money is recorded; the buyer stays unpaid.

The fix re-reads the Subscription fresh on exactly that duplicate-at-the-ledger branch, and falls
through to steps 3/4 when the transition is PROVABLY still pending (computed the same way
``_apply_transition`` decides its own ``permitted``, minus the ``renewal_in_place`` addition for a
payment - deliberately: an already-``active`` row is where resuming could extend a period a
second time for one payment, and this file proves that case stays untouched).
"""

from __future__ import annotations

from typing import List

from backend_app.backend.marketplace.settlement_service import (
    SETTLEMENT_REFERENCE_UNIQUE_CONSTRAINT,
    SettlementOutcome,
)
from backend_app.backend.marketplace.subscription_period import period_for_activation
from tests.test_settlement_service import (
    AMOUNT_1999,
    CONFIRMED_AT,
    LISTING_ID,
    OWNER_ID,
    OWNER_SHARE_1999,
    PLATFORM_FEE_1999,
    PROVIDER,
    PURCHASER_ID,
    REFERENCE,
    SUBSCRIPTION_ID,
    FakeSupabase,
    RecordingAuditLogger,
    RecordingGrant,
    _settle,
    _subscription_row,
    audit,  # noqa: F401 -- pytest fixture, used by name via test parameters
    no_sleep,  # noqa: F401 -- pytest fixture, used by name via test parameters
)


def _legacy_settlement_row(
    *, settlement_id: str, is_reversal: bool = False, settled_at=CONFIRMED_AT
) -> dict:
    """A ``marketplace_settlements`` row shaped exactly as ``_settlement_payload`` would
    build it, for seeding ``FakeSupabase(settlements=[...])`` directly -- i.e. AS IF it
    had been written by a process that predates MKT-03's atomic
    ``record_and_transition_settlement`` (050_mkt03_settlement_atomic_transition.sql),
    or by any other path this migration cannot reach. A FRESH ``settle()`` call can no
    longer produce "ledger row exists, transition does not" on its own: the insert and
    the transition are now one transaction, so a test proving the DUPLICATE_RESUMED
    fallback still repairs this state has to construct it directly, exactly as
    ``test_an_already_active_redelivery_is_not_auto_extended`` already does for its own
    (different) pre-existing-ledger-row scenario.
    """
    return {
        "id": settlement_id,
        "subscription_id": SUBSCRIPTION_ID,
        "listing_id": LISTING_ID,
        "owner_id": OWNER_ID,
        "purchaser_id": PURCHASER_ID,
        "amount_minor": AMOUNT_1999,
        "owner_share_minor": OWNER_SHARE_1999,
        "platform_fee_minor": PLATFORM_FEE_1999,
        "currency": "USD",
        "provider": PROVIDER,
        "provider_reference": REFERENCE,
        "is_reversal": is_reversal,
        "reverses_reference": REFERENCE if is_reversal else None,
        "settled_at": settled_at.isoformat(),
    }


def test_a_redelivery_after_the_transition_never_landed_completes_it(
    audit: RecordingAuditLogger, no_sleep: List[int]
) -> None:
    """THE MKT-03 SCENARIO, AS IT CAN STILL ARISE: a ledger row exists with no matching
    transition, and a redelivery must complete it rather than reporting a bare duplicate.

    UPDATED FOR THE ATOMIC REDESIGN (050_mkt03_settlement_atomic_transition.sql). A
    FRESH ``settle()`` call can no longer PRODUCE this state itself any more: the
    ledger insert and the transition UPDATE are now one ``record_and_transition_
    settlement`` call, so either both land or neither does (proven directly by
    ``tests/test_settlement_service.py::
    test_a_ledger_insert_that_never_completes_leaves_the_ledger_unchanged`` and its
    sibling for the UPDATE sub-step). So this test now seeds the stuck state exactly as
    it would exist on a database carrying a row from BEFORE this fix shipped -- the
    scenario the fallback exists for -- rather than trying to make a fresh atomic call
    produce it, which it structurally cannot.
    """
    client = FakeSupabase(
        subscriptions=[_subscription_row(status="pending")],
        settlements=[_legacy_settlement_row(settlement_id="settlement-legacy-1")],
    )
    grant = RecordingGrant()

    assert len(client.settlements) == 1, "the pre-existing ledger row must be seeded"
    assert client.subscription()["status"] == "pending", (
        "the transition must not have landed -- that is the premise of this test"
    )
    assert client.transitions == []
    assert grant.calls == []

    result = _settle(client, grant=grant)

    assert result.outcome is SettlementOutcome.RECORDED, (
        f"a redelivery that found the transition still pending must complete it, not report a "
        f"bare duplicate; got {result.outcome}"
    )
    assert len(client.settlements) == 1, "the redelivery must not write a second ledger row"
    assert len(client.transitions) == 1, "the redelivery must write the missing transition row"
    assert len(grant.calls) == 1, "the redelivery must grant the missing entitlement"

    expected_start, expected_expiry = period_for_activation(CONFIRMED_AT)
    stored = client.subscription()
    assert stored["status"] == "active"
    assert stored["period_start"] == expected_start.isoformat()
    assert stored["period_expiry"] == expected_expiry.isoformat()
    assert result.period_written is True
    assert result.entitlement_granted is True
    assert result.settlement_id is not None, (
        "the resumed result must name the Settlement that actually carries the money"
    )

    names = audit.action_names()
    assert "MARKETPLACE_SETTLEMENT_DUPLICATE_RESUMED" in names, (
        "an operator must be able to see that a redelivery found and repaired a stuck "
        "settlement, distinct from an ordinary duplicate-ignored no-op"
    )
    resumed_metadata = audit.metadata_for("MARKETPLACE_SETTLEMENT_DUPLICATE_RESUMED")
    assert resumed_metadata["provider_reference"] == REFERENCE
    assert resumed_metadata["constraint"] == SETTLEMENT_REFERENCE_UNIQUE_CONSTRAINT
    assert "MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED" not in names, (
        "this redelivery resumed pending work; it did not merely ignore a duplicate"
    )


def test_a_redelivery_after_full_completion_is_still_ignored(
    audit: RecordingAuditLogger,
) -> None:
    """The counter-test: a redelivery of an ALREADY fully-applied settlement changes nothing.

    No regression on the case this module already protected: once the transition and the
    entitlement have both landed, a further redelivery must stay ``DUPLICATE_IGNORED`` and must
    not touch the period a second time.
    """
    client = FakeSupabase(subscriptions=[_subscription_row(status="pending")])
    grant = RecordingGrant()

    first = _settle(client, grant=grant)
    assert first.outcome is SettlementOutcome.RECORDED
    stored_after_first = dict(client.subscription())

    second = _settle(client, grant=grant)

    assert second.outcome is SettlementOutcome.DUPLICATE_IGNORED
    assert len(client.settlements) == 1
    assert len(client.transitions) == 1, "a fully-applied redelivery must not add a second row"
    assert len(grant.calls) == 1, "a fully-applied redelivery must not grant a second time"
    assert client.subscription() == stored_after_first, (
        "a redelivery of an already-completed settlement must not touch the period"
    )
    assert "MARKETPLACE_SETTLEMENT_DUPLICATE_RESUMED" not in audit.action_names()


def test_an_already_active_redelivery_is_not_auto_extended(
    audit: RecordingAuditLogger,
) -> None:
    """The deliberately-excluded ambiguous case: already active, ledger row present.

    When the ledger row for this reference already exists AND the Subscription already reads
    ``active`` with a stored period, a redelivery must not re-extend it: there is no way to tell
    "this exact settlement already activated it" apart from "a separate, later renewal already
    extended it", and resuming here risks a double extension for one payment. The safe answer is
    the existing one: ``DUPLICATE_IGNORED``, period untouched.
    """
    existing_start, existing_expiry = period_for_activation(CONFIRMED_AT)
    client = FakeSupabase(
        subscriptions=[
            _subscription_row(
                status="active",
                period_start=existing_start.isoformat(),
                period_expiry=existing_expiry.isoformat(),
            )
        ],
        settlements=[
            {
                "id": "settlement-already-applied",
                "subscription_id": "sub-1",
                "provider_reference": REFERENCE,
                "is_reversal": False,
                "amount_minor": AMOUNT_1999,
            }
        ],
    )
    grant = RecordingGrant()

    result = _settle(client, grant=grant)

    assert result.outcome is SettlementOutcome.DUPLICATE_IGNORED
    assert len(client.transitions) == 0
    assert len(grant.calls) == 0
    stored = client.subscription()
    assert stored["period_start"] == existing_start.isoformat()
    assert stored["period_expiry"] == existing_expiry.isoformat(), (
        "an already-active redelivery must not extend the period a second time"
    )


def test_a_reversal_redelivery_after_the_refund_never_landed_completes_it(
    audit: RecordingAuditLogger, no_sleep: List[int]
) -> None:
    """The reversal mirror of the core case: the refund row exists, the status move does not.

    Seeded directly, for the same reason the payment case above now is: a fresh
    ``settle(is_reversal=True)`` call cannot itself produce "refund row recorded,
    subscription still active" any more -- the atomic function makes that one
    transaction.
    """
    client = FakeSupabase(
        subscriptions=[
            _subscription_row(
                status="active",
                period_start=CONFIRMED_AT.isoformat(),
                period_expiry=CONFIRMED_AT.isoformat(),
            )
        ],
        settlements=[
            _legacy_settlement_row(settlement_id="settlement-legacy-refund-1", is_reversal=True)
        ],
    )

    assert client.subscription()["status"] == "active", (
        "the refund transition must not have landed -- that is the premise of this test"
    )

    result = _settle(client, is_reversal=True)

    assert result.outcome is SettlementOutcome.REVERSED, (
        f"a reversal redelivery that found the refund still pending must complete it; got "
        f"{result.outcome}"
    )
    assert client.subscription()["status"] == "refunded"
    assert len(client.settlements) == 1
    names = audit.action_names()
    assert "MARKETPLACE_SETTLEMENT_DUPLICATE_RESUMED" in names


def test_a_reversal_redelivery_after_full_completion_is_still_ignored(
    audit: RecordingAuditLogger,
) -> None:
    client = FakeSupabase(
        subscriptions=[
            _subscription_row(
                status="active",
                period_start=CONFIRMED_AT.isoformat(),
                period_expiry=CONFIRMED_AT.isoformat(),
            )
        ],
    )

    first = _settle(client, is_reversal=True)
    assert first.outcome is SettlementOutcome.REVERSED

    second = _settle(client, is_reversal=True)

    assert second.outcome is SettlementOutcome.DUPLICATE_IGNORED
    assert client.subscription()["status"] == "refunded"
    assert "MARKETPLACE_SETTLEMENT_DUPLICATE_RESUMED" not in audit.action_names()
