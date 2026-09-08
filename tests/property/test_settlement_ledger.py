"""Property tests for the Settlement_Ledger's arithmetic: P-5 and P-7.

Feature: marketplace-subscriptions-paper-trading
Design reference: ``design.md § Property-to-test mapping`` ->
``P-5 | tests/property/test_settlement_ledger.py | generated settlement sequences |
independent per-currency accumulation`` and
``P-7 | tests/property/test_settlement_ledger.py | invalid amounts | refusal, ledger unchanged``.

Modules under test
------------------
``backend_app/backend/marketplace/settlement_service.py`` (task 19.1) - the one
Settlement_Record writer, and therefore the only thing that can put a number into an owner's
earnings figure.
``backend_app/backend/marketplace/money.py`` (task 4.1) - the one 90/10 split.

Properties living here
----------------------
``test_p5_reported_totals_equal_ledger_sums``  (task 19.5, Requirements 10.5, 10.6, 10.7)
``test_p7_invalid_amounts_are_refused``        (task 19.7, Requirements 10.1, 10.5)

WHY THE LEDGER IS EXERCISED THROUGH ``settle`` AND NOT THROUGH HAND-WRITTEN ROWS
-------------------------------------------------------------------------------
Requirement 10.6 says every earnings figure is derived by *summing persisted
Settlement_Records*. A test that inserted its own rows and then summed them would assert its own
arithmetic twice and say nothing about the production path: the interesting question is whether
the rows ``settlement_service.settle`` persists carry the components Requirement 10.7's total is
supposed to be made of. So the sequence is driven through ``settle``, against the Supabase double
from ``tests/test_settlement_service.py`` - reused rather than reinvented, because that double
**enforces** ``uq_settlement_reference_reversal UNIQUE (provider_reference, is_reversal)``. A
double that accepted every insert would let a redelivery add a second row, and a totals property
asserted against it would be worthless: the number would match an oracle that also double-counted
only by coincidence, and the constraint that actually protects the total would be untested.

WHY THE ORACLE DOES NOT CALL ``split_ninety_ten``
------------------------------------------------
The oracle accumulates over the *generated* sequence - the inputs - and derives each owner share
by exact decimal division floored to an integer (:func:`_owner_share_oracle`), which shares no
operator with the implementation's ``(amount * 90) // 100``. Calling the production split here
would make P-5 a tautology about addition: a wrong split would be summed identically on both
sides and the totals would agree. The remainder characterisation
``0 <= amount*90 - 100*owner_share < 100`` is asserted on top, per row, so "the owner share" means
one specific integer rather than "whatever the code returned".

WHY NO ``float`` AND NO ``Decimal`` APPEARS IN AN ASSERTED VALUE
---------------------------------------------------------------
Requirement 10.3 forbids binary floating-point money arithmetic, and Requirement 10.7 wants an
*exact integer* total in Minor_Units. Every asserted quantity in this module is a Python ``int``.
``Decimal`` appears in exactly one place - inside :func:`_owner_share_oracle`, as the independent
division mechanism - and its result is converted to ``int`` before it is ever summed, so no total
is ever accumulated in a fractional type.

WHAT IS DELIBERATELY NOT ASSERTED HERE
--------------------------------------
* Duplicate-confirmation idempotence as such - the "one row, one period expiry" claim is P-6's
  (``tests/property/test_settlement_idempotence.py``, task 19.6). Redeliveries appear in P-5's
  generated sequence only because a total that double-counted one would be wrong, which is a
  statement about the total.
* The Subscription_State machine (P-8 … P-10), the expiry boundary (P-11) and history retention
  (P-15). Those are tasks 19.8 … 19.12 and live in their own modules.
* Anything about ``marketplace_listings`` or ``strategy_subscriptions``: both stay dormant
  (Requirement 1.2) and neither is referenced here.
* The HTTP surface. ``settle`` is called directly; no TestClient, no router, no network.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal, localcontext
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple
from unittest import mock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.marketplace import settlement_service as ss
from backend_app.backend.marketplace.money import (
    MAX_AMOUNT_MINOR,
    OWNER_SHARE_PERCENT,
    InvalidAmount,
    split_ninety_ten,
)
from backend_app.backend.marketplace.settlement_service import (
    SETTLEMENT_TABLE,
    SettlementOutcome,
    settle,
)

# The Supabase double, the entitlement recorder, the audit recorder and the loop runner all
# already exist for task 19.1's unit suite. Importing them keeps ONE double for this path -
# the alternative is a third fake whose enforcement of uq_settlement_reference_reversal would
# have to be trusted separately. Only non-test names are imported, so nothing is re-collected.
from tests.test_settlement_service import (
    FakeSupabase,
    RecordingAuditLogger,
    RecordingGrant,
    _run_coroutine,
)
from tests.strategies.marketplace_generators import CURRENCIES, minor_amounts

#: The configuration ``design.md § Property-based testing configuration`` prescribes, matching
#: ``tests/property/test_money_split.py``: at least 100 examples where an example is cheap, no
#: per-example deadline, ``derandomize`` left at its default so ``.hypothesis`` keeps
#: accumulating failing examples.
#:
#: ``deadline=None`` and not a millisecond budget, deliberately: every example here runs an
#: ``async`` entry point on a freshly created event loop, and loop setup/teardown jitter on a
#: loaded machine is easily an order of magnitude wider than the work being measured. A deadline
#: would turn that jitter into a flaky failure that says nothing about the ledger. Run time is
#: bounded by ``max_examples`` and by the sequence generator's own caps instead (at most four
#: Subscriptions and at most two deliveries per confirmation, so at most twelve ``settle`` calls
#: per example), which keeps the whole module well inside a minute.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

#: P-5 drives a whole sequence per example, so it gets a smaller budget than the single-call
#: properties. Fifty examples over sequences of up to four Subscriptions in two currencies with
#: redeliveries and reversals is a few hundred ``settle`` calls in total.
LEDGER_SEQUENCE_SETTINGS = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

#: Two owners, so "per owner, per currency" is a real partition of the ledger rather than a
#: single bucket every row falls into.
OWNER_IDS: Tuple[str, ...] = ("owner-1", "owner-2")

PROVIDER = "stripe"

#: One fixed confirmation instant. The period arithmetic is P-12's and P-14's; fixing the instant
#: here keeps P-5 about the money and keeps every example's period computation identical.
CONFIRMED_AT = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# The oracle: an independent owner share, and an independent accumulation
# ══════════════════════════════════════════════════════════════════════════


def _owner_share_oracle(amount_minor: int) -> int:
    """The owner's share of ``amount_minor``, derived without ``//``.

    Ninety percent of the amount by *exact decimal division*, floored to an integer. That is a
    different mechanism from the implementation's ``(amount_minor * 90) // 100``, which is the
    whole point: an oracle that restated the implementation's expression would agree with a wrong
    one. ``Decimal`` is exact here - ``amount_minor * 90`` has at most thirteen digits at the top
    of Requirement 10.1's domain and the working precision is forty - and the result is converted
    to ``int`` immediately, so no fractional value is ever summed into a total.

    The closing assertion is the defining characterisation of the quotient: ``owner_share`` is the
    truncated-toward-zero ninety percent exactly when the remainder lies in ``[0, 100)``. It is
    written with multiplication and subtraction only, so it shares no operator with either the
    implementation or the division above.
    """
    with localcontext() as ctx:
        ctx.prec = 40
        exact_ninety_percent = (
            Decimal(amount_minor) * Decimal(OWNER_SHARE_PERCENT) / Decimal(100)
        )
        floored = exact_ninety_percent.to_integral_value(rounding=ROUND_FLOOR)
    owner_share = int(floored)

    remainder = amount_minor * OWNER_SHARE_PERCENT - 100 * owner_share
    assert 0 <= remainder < 100, (
        f"the oracle itself is wrong for {amount_minor}: "
        f"{amount_minor}*{OWNER_SHARE_PERCENT} - 100*{owner_share} = {remainder}, "
        "which is outside [0, 100)"
    )
    return owner_share


def _platform_fee_oracle(amount_minor: int) -> int:
    """The platform's residual share: whatever the amount is, less the owner's share."""
    return amount_minor - _owner_share_oracle(amount_minor)


# ══════════════════════════════════════════════════════════════════════════
# The reported total: Requirement 10.7's formula, read off the persisted ledger
# ══════════════════════════════════════════════════════════════════════════


def _ledger_rows(
    client: FakeSupabase, *, owner_id: Optional[str] = None, currency: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Read Settlement_Records back through the same query surface production uses.

    Filtering happens in the Persistence_Layer double (``.eq(...)``), not in a Python
    comprehension over ``client.settlements``, so a read that silently ignored its currency
    predicate would be visible here rather than papered over by the test doing the filtering
    itself.
    """
    query = client.table(SETTLEMENT_TABLE).select(ss.SETTLEMENT_PAYMENT_SELECT)
    if owner_id is not None:
        query = query.eq("owner_id", owner_id)
    if currency is not None:
        query = query.eq("currency", currency)
    return list(query.execute().data or [])


def _reported_totals(rows: Sequence[Dict[str, Any]]) -> Tuple[int, int]:
    """Requirement 10.7's totals over ``rows``: ``(owner_total, platform_total)``.

    "The sum of ``owner_share`` over non-reversal Settlement_Records minus the sum of
    ``owner_share`` over reversal Settlement_Records", in exact integer Minor_Units, before any
    presentation formatting - and the same for ``platform_fee``. Nothing is grouped here: the
    caller passes rows already restricted to one currency, because combining currencies is what
    Requirement 10.7 forbids.
    """
    owner_total = 0
    platform_total = 0
    for row in rows:
        sign = -1 if bool(row.get("is_reversal")) else 1
        owner_total += sign * int(row["owner_share_minor"])
        platform_total += sign * int(row["platform_fee_minor"])
    return owner_total, platform_total


# ══════════════════════════════════════════════════════════════════════════
# The doubles' wiring
# ══════════════════════════════════════════════════════════════════════════


@contextmanager
def _recording_audit() -> Iterator[RecordingAuditLogger]:
    """Install a recording audit logger for the duration of one example.

    ``settlement_service._write_audit`` imports ``get_strategy_audit_logger`` lazily from
    ``backend_app.core.audit_trail`` at call time, so patching the module attribute is enough, and
    the real ``StrategyAuditAction`` enum still resolves every action name - which is what proves
    the five names exist rather than assuming it.

    Patched per example rather than through the ``monkeypatch`` fixture on purpose: a
    function-scoped fixture is installed once for the whole Hypothesis run, and a recorder shared
    across examples would accumulate another example's acts into this one's assertions.
    """
    from backend_app.core import audit_trail

    recorder = RecordingAuditLogger()
    with mock.patch.object(audit_trail, "get_strategy_audit_logger", lambda: recorder):
        yield recorder


def _subscription_row(
    *,
    row_id: str,
    owner_id: str,
    price_minor: int,
    currency: str,
    status: str = "pending",
) -> Dict[str, Any]:
    """One ``library_subscriptions`` row, carrying exactly the columns the settlement path reads.

    The column set is ``settlement_service.SETTLEMENT_SUBSCRIPTION_SELECT``'s; a row missing one
    of them would make the double disagree with the projection the production read requests.
    """
    return {
        "id": row_id,
        "library_id": f"listing-{row_id}",
        "user_id": f"purchaser-{row_id}",
        "owner_id": owner_id,
        "status": status,
        "price_minor": price_minor,
        "currency": currency,
        "period_start": None,
        "period_expiry": None,
        "provider": PROVIDER,
        "provider_reference": None,
    }


def _settle(
    client: FakeSupabase,
    *,
    reference: str,
    amount_minor: Any,
    currency: str,
    subscription_id: str,
    is_reversal: bool = False,
    grant: Any,
) -> Any:
    """One ``settle`` call, on its own event loop (never bare ``asyncio.run``)."""
    return _run_coroutine(
        settle(
            provider_reference=reference,
            provider=PROVIDER,
            amount_minor=amount_minor,
            currency=currency,
            subscription_id=subscription_id,
            confirmation_instant=CONFIRMED_AT,
            supabase=client,
            is_reversal=is_reversal,
            reverses_reference=reference if is_reversal else None,
            grant_permission=grant,
        )
    )


# ══════════════════════════════════════════════════════════════════════════
# The generator: a sequence of confirmations over several Subscriptions
# ══════════════════════════════════════════════════════════════════════════


@st.composite
def _settlement_sequences(draw: Any, max_subscriptions: int = 4) -> Dict[str, Any]:
    """A sequence of payment and reversal confirmations across several Subscriptions.

    What varies, and why each part is there:

    * **the currency per Subscription**, drawn from the two with a declared Minor_Unit exponent,
      so a sequence normally mixes currencies - which is the only way "never combined into a
      single total" (Requirement 10.7) can fail visibly;
    * **the owner per Subscription**, so the totals are a partition rather than one bucket;
    * **the amount**, from ``minor_amounts()`` - the boundaries 0, 1, 99, 100, 101 and
      ``MAX_AMOUNT_MINOR`` are in its pool, so the rounding edge where the remainder falls to the
      platform is hit on nearly every run;
    * **redeliveries** of one provider reference, because a total that counted a redelivered
      confirmation twice would be wrong (the "exactly one row" claim itself is P-6's);
    * **reversals**, at the full recorded amount, because Requirement 10.7 subtracts them; a
      *partial* refund is deliberately absent, since ``settle`` refuses it as ``MISMATCHED`` and
      writes nothing, which is a guard assertion rather than a totals one;
    * **the order** of both phases, so the totals cannot depend on the sequence being sorted.

    Reversals always follow every payment: a refund of a payment that has not been confirmed is
    not a sequence this ledger can produce, and generating one would test the guard rather than
    the arithmetic.
    """
    count = draw(st.integers(min_value=1, max_value=max_subscriptions))

    subscriptions: List[Dict[str, Any]] = []
    for index in range(count):
        subscriptions.append(
            {
                "id": f"sub-{index}",
                "reference": f"pi_generated_{index}",
                "owner_id": draw(st.sampled_from(OWNER_IDS)),
                "currency": draw(st.sampled_from(CURRENCIES)),
                "price_minor": draw(minor_amounts()),
                "payment_deliveries": draw(st.integers(min_value=1, max_value=2)),
                "is_reversed": draw(st.booleans()),
                "reversal_deliveries": draw(st.integers(min_value=1, max_value=2)),
            }
        )

    payment_order = draw(st.permutations([row["id"] for row in subscriptions]))
    reversal_order = draw(
        st.permutations([row["id"] for row in subscriptions if row["is_reversed"]])
    )
    return {
        "subscriptions": subscriptions,
        "payment_order": list(payment_order),
        "reversal_order": list(reversal_order),
    }


# ══════════════════════════════════════════════════════════════════════════
# P-5
# ══════════════════════════════════════════════════════════════════════════

# Feature: marketplace-subscriptions-paper-trading, Property 5 (invariant, ledger sum):
# For all generated sequences of payment and reversal confirmations, the reported owner earnings
# equal the exact integer sum of owner_share over the persisted Settlement_Records and the
# reported platform total the sum of platform_fee, PER CURRENCY, with reversals subtracted and
# Settlement_Records of different currencies never combined into one total.
@LEDGER_SEQUENCE_SETTINGS
@given(sequence=_settlement_sequences())
def test_p5_reported_totals_equal_ledger_sums(sequence: Dict[str, Any]) -> None:
    """Every reported total is the ledger, summed - per currency, reversals subtracted.

    This is the property that makes Requirement 10.6 checkable. The defect it exists to exclude
    is the one the audit found in ``creator_analytics``: an earnings figure derived from
    ``clone_count`` multiplied by a current price, in floats, from columns that do not exist. A
    figure computed that way can be *any* number; a figure that is provably the integer sum of
    the persisted splits can only be the money that actually moved.

    Four claims, each asserted separately because each fails on its own:

    1. **Per row**, the persisted components are the 90/10 split of the persisted amount:
       ``owner_share + platform_fee == amount`` exactly, and ``owner_share`` is the largest whole
       Minor_Unit count not exceeding ninety percent - so the truncation remainder always lands on
       the platform, deterministically, and never exceeds one Minor_Unit.
    2. **Per (owner, currency)**, the reported owner total equals the independently accumulated
       oracle total, and likewise the platform total. Reversals are subtracted.
    3. **Currencies are never combined**: a read restricted to one currency returns rows of that
       currency only, no row is converted (each row's amount is its Subscription's recorded
       ``price_minor`` in its recorded currency), and the per-currency totals decompose the
       currency-blind sum exactly - nothing lost, nothing double-counted.
    4. **The sequence actually settled**: every confirmation's outcome is asserted, so the totals
       cannot agree by both being empty.

    **Validates: Requirements 10.5, 10.6, 10.7**
    """
    subscriptions: List[Dict[str, Any]] = sequence["subscriptions"]
    by_id = {row["id"]: row for row in subscriptions}

    client = FakeSupabase(
        subscriptions=[
            _subscription_row(
                row_id=row["id"],
                owner_id=row["owner_id"],
                price_minor=row["price_minor"],
                currency=row["currency"],
            )
            for row in subscriptions
        ]
    )
    grant = RecordingGrant()

    # ── The oracle. Accumulated over the GENERATED SEQUENCE - the inputs - keyed by
    #    (owner, currency), with reversals subtracted. It never reads the ledger and never calls
    #    the production split. ──
    owner_oracle: Dict[Tuple[str, str], int] = {}
    platform_oracle: Dict[Tuple[str, str], int] = {}
    expected_row_count = 0

    with _recording_audit():
        # ── Phase 1: the payments, in the drawn order. ──
        for subscription_id in sequence["payment_order"]:
            row = by_id[subscription_id]
            for delivery in range(1, row["payment_deliveries"] + 1):
                result = _settle(
                    client,
                    reference=row["reference"],
                    amount_minor=row["price_minor"],
                    currency=row["currency"],
                    subscription_id=subscription_id,
                    grant=grant,
                )
                if delivery == 1:
                    assert result.outcome is SettlementOutcome.RECORDED, (
                        f"the first delivery of {row['reference']} did not record: "
                        f"{result.outcome}"
                    )
                    key = (row["owner_id"], row["currency"])
                    owner_oracle[key] = owner_oracle.get(key, 0) + _owner_share_oracle(
                        row["price_minor"]
                    )
                    platform_oracle[key] = platform_oracle.get(
                        key, 0
                    ) + _platform_fee_oracle(row["price_minor"])
                    expected_row_count += 1
                else:
                    # uq_settlement_reference_reversal refused the second insert. It adds no row,
                    # so it must add nothing to any total either.
                    assert result.outcome is SettlementOutcome.DUPLICATE_IGNORED, (
                        f"delivery {delivery} of {row['reference']} was not ignored: "
                        f"{result.outcome}"
                    )

        # ── Phase 2: the reversals, in the drawn order, at the full recorded amount. ──
        for subscription_id in sequence["reversal_order"]:
            row = by_id[subscription_id]
            for delivery in range(1, row["reversal_deliveries"] + 1):
                result = _settle(
                    client,
                    reference=row["reference"],
                    amount_minor=row["price_minor"],
                    currency=row["currency"],
                    subscription_id=subscription_id,
                    is_reversal=True,
                    grant=grant,
                )
                if delivery == 1:
                    assert result.outcome is SettlementOutcome.REVERSED, (
                        f"the reversal of {row['reference']} was not recorded: {result.outcome}"
                    )
                    key = (row["owner_id"], row["currency"])
                    owner_oracle[key] = owner_oracle.get(key, 0) - _owner_share_oracle(
                        row["price_minor"]
                    )
                    platform_oracle[key] = platform_oracle.get(
                        key, 0
                    ) - _platform_fee_oracle(row["price_minor"])
                    expected_row_count += 1
                else:
                    assert result.outcome is SettlementOutcome.DUPLICATE_IGNORED, (
                        f"reversal delivery {delivery} of {row['reference']} was not ignored: "
                        f"{result.outcome}"
                    )

    # ── Claim 4, first: the sequence actually produced rows, so nothing below is vacuous. ──
    persisted = _ledger_rows(client)
    assert len(persisted) == expected_row_count, (
        f"the ledger holds {len(persisted)} rows but {expected_row_count} confirmations were "
        "recorded; a redelivery added a row, or a confirmation wrote none"
    )
    assert expected_row_count >= 1, "the generated sequence settled nothing"

    # ── Claim 1: per row, the split conserves and the remainder lands on the platform. ──
    for ledger_row in persisted:
        amount = int(ledger_row["amount_minor"])
        owner_share = int(ledger_row["owner_share_minor"])
        platform_fee = int(ledger_row["platform_fee_minor"])

        for name, component in (
            ("amount_minor", amount),
            ("owner_share_minor", owner_share),
            ("platform_fee_minor", platform_fee),
        ):
            stored = ledger_row[name]
            assert isinstance(stored, int) and not isinstance(stored, bool), (
                f"{name} must be persisted as an int number of Minor_Units, got "
                f"{type(stored).__name__}: {stored!r}"
            )
            assert component >= 0, f"{name} is negative: {component}"

        assert owner_share + platform_fee == amount, (
            f"row {ledger_row['provider_reference']} does not conserve: "
            f"{owner_share} + {platform_fee} = {owner_share + platform_fee} != {amount}"
        )
        assert owner_share == _owner_share_oracle(amount), (
            f"row {ledger_row['provider_reference']} persisted owner_share {owner_share} for "
            f"amount {amount}; the truncated ninety percent is {_owner_share_oracle(amount)}"
        )
        # The remainder is deterministic and it is the platform's: owner_share is the largest
        # whole Minor_Unit count at or below ninety percent, so at most one Minor_Unit of the
        # amount is carried by the fee beyond its exact ten percent.
        assert owner_share * 100 <= amount * OWNER_SHARE_PERCENT, (
            f"owner_share {owner_share} exceeds ninety percent of {amount}"
        )
        assert (owner_share + 1) * 100 > amount * OWNER_SHARE_PERCENT, (
            f"owner_share {owner_share} is not the largest whole Minor_Unit count at or below "
            f"ninety percent of {amount}"
        )

    # ── Claim 3, first half: no row was converted, and each carries its own currency. ──
    for ledger_row in persisted:
        source = by_id[str(ledger_row["subscription_id"])]
        assert int(ledger_row["amount_minor"]) == source["price_minor"], (
            f"row {ledger_row['provider_reference']} persisted amount "
            f"{ledger_row['amount_minor']} for a Subscription recording "
            f"{source['price_minor']}"
        )
        assert ledger_row["currency"] == source["currency"], (
            f"row {ledger_row['provider_reference']} persisted currency "
            f"{ledger_row['currency']!r} for a Subscription recording "
            f"{source['currency']!r}; currencies are never converted"
        )

    # ── Claim 2: per (owner, currency), the reported totals are the oracle's. ──
    groups = {(row["owner_id"], row["currency"]) for row in subscriptions}
    for owner_id, currency in sorted(groups):
        rows = _ledger_rows(client, owner_id=owner_id, currency=currency)

        # Claim 3, second half: the restricted read really is restricted.
        assert {r["currency"] for r in rows} <= {currency}, (
            f"the {currency} read for {owner_id} returned rows in "
            f"{sorted({r['currency'] for r in rows})}"
        )
        assert {r["owner_id"] for r in rows} <= {owner_id}, (
            f"the read for {owner_id} returned another owner's rows"
        )

        owner_total, platform_total = _reported_totals(rows)
        expected_owner = owner_oracle.get((owner_id, currency), 0)
        expected_platform = platform_oracle.get((owner_id, currency), 0)

        assert owner_total == expected_owner, (
            f"reported owner earnings for {owner_id} in {currency} are {owner_total} "
            f"Minor_Units; the ledger sum of owner_share (reversals subtracted) is "
            f"{expected_owner}, a discrepancy of {owner_total - expected_owner}"
        )
        assert platform_total == expected_platform, (
            f"reported platform total for {owner_id} in {currency} is {platform_total} "
            f"Minor_Units; the ledger sum of platform_fee (reversals subtracted) is "
            f"{expected_platform}, a discrepancy of {platform_total - expected_platform}"
        )
        assert isinstance(owner_total, int) and isinstance(platform_total, int)

    # ── Claim 3, third half: the per-currency totals decompose the whole ledger exactly, so a
    #    currency was neither dropped from its own total nor folded into another's. ──
    blind_owner, blind_platform = _reported_totals(persisted)
    assert blind_owner == sum(owner_oracle.values()), (
        f"the ledger's owner_share sum is {blind_owner} but the per-currency totals add to "
        f"{sum(owner_oracle.values())}; a row is missing from its currency's total or counted "
        "in another's"
    )
    assert blind_platform == sum(platform_oracle.values()), (
        f"the ledger's platform_fee sum is {blind_platform} but the per-currency totals add to "
        f"{sum(platform_oracle.values())}"
    )

    # Reversals are subtracted, not added: a ledger holding a reversal can only report a total at
    # or below the same ledger with the reversal removed.
    if any(row["is_reversed"] for row in subscriptions):
        payments_only = [r for r in persisted if not bool(r["is_reversal"])]
        payments_owner_total, _ = _reported_totals(payments_only)
        assert blind_owner <= payments_owner_total, (
            f"the reversals raised the reported owner total from {payments_owner_total} to "
            f"{blind_owner}; Requirement 10.7 subtracts them"
        )


# ══════════════════════════════════════════════════════════════════════════
# P-7
# ══════════════════════════════════════════════════════════════════════════

#: Every shape of inadmissible amount Requirement 10.1's domain excludes.
#:
#: ``bool`` is in the pool because ``isinstance(True, int)`` is ``True`` in Python, so without an
#: explicit refusal ``True`` would settle as one Minor_Unit. ``float`` is in the pool in three
#: flavours - fractional, integral-valued and non-finite - because the interesting failure is not
#: a crash but a *silent* ``int(19.99) -> 19`` or ``round(19.99) -> 20``: an amount nobody
#: charged, recorded as though they had. ``Decimal`` and ``str`` are refused for the same reason
#: they are at the ingestion boundary: exactness this module cannot vouch for.
_INADMISSIBLE_AMOUNTS = st.one_of(
    # Negative: Requirement 10.5's ``amount >= 0`` CHECK, refused before it is ever reached.
    st.integers(min_value=-MAX_AMOUNT_MINOR, max_value=-1),
    st.sampled_from([-1, -100, -1999, -MAX_AMOUNT_MINOR]),
    # Over the maximum of Requirement 10.1's inclusive domain.
    st.integers(min_value=MAX_AMOUNT_MINOR + 1, max_value=MAX_AMOUNT_MINOR * 1000),
    st.sampled_from([MAX_AMOUNT_MINOR + 1, MAX_AMOUNT_MINOR * 10]),
    # Non-integer: a float that would truncate or round to something plausible, and one that
    # would not survive int() at all.
    st.sampled_from([19.99, 1999.0, 0.0, -0.5, float("nan"), float("inf")]),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.sampled_from([Decimal("1999"), Decimal("19.99")]),
    st.booleans(),
    st.sampled_from(["1999", "19.99", None, (1999,)]),
)

#: The control amount: admissible, and the price the Subscription in P-7's fixture recorded.
_VALID_AMOUNT = 1999
_VALID_CURRENCY = "USD"
_CONTROL_SUBSCRIPTION_ID = "sub-p7"
_CONTROL_OWNER_ID = "owner-p7"


# Feature: marketplace-subscriptions-paper-trading, Property 7 (error-condition):
# For all negative, non-integer and over-maximum amounts: the settlement is refused and NO
# Settlement_Record is written.
@PROPERTY_SETTINGS
@given(amount=_INADMISSIBLE_AMOUNTS)
def test_p7_invalid_amounts_are_refused(amount: Any) -> None:
    """An inadmissible amount is refused, and refused *before* anything is written.

    The failure mode this excludes is not an exception nobody catches - it is the absence of one.
    A ``float`` amount coerced with ``int()`` charges 19 for a 19.99 Listing (the defect the audit
    found in ``create_marketplace_checkout``'s ``int(float(price) * 100)``), and a negative or
    over-maximum amount coerced into range invents a figure the ledger then treats as money that
    moved. So the assertion is in two halves:

    * the arithmetic refuses the amount at ``money.split_ninety_ten`` - the one place the split
      exists, so no caller can reach a different answer; and
    * ``settle`` propagates that refusal having issued **no statement at all**, on any table. Not
      "no settlement row" - no read, no write, no audit, no entitlement. ``settle`` takes the
      split at its boundary before it touches the Persistence_Layer, which is what makes this
      property about ordering rather than about cleanup.

    Requirement 10.5's ``CHECK`` constraints (``amount >= 0``, ``owner_share >= 0``,
    ``platform_fee >= 0``, ``owner_share + platform_fee = amount``) are the Persistence_Layer's
    last line, and a negative amount would violate the first of them. This property asserts the
    refusal happens early enough that the constraint is never even exercised - a webhook path that
    relied on a ``23514`` coming back would have already written its audit line and, worse, would
    read the integrity error as a transient failure and retry it five times.

    The control settlement at the end is what stops the whole property from being vacuous: the
    same double, the same Subscription and the same call shape record exactly one row when the
    amount *is* admissible, so "no row was written" means the amount was refused rather than that
    nothing works.

    **Validates: Requirements 10.1, 10.5**
    """
    client = FakeSupabase(
        subscriptions=[
            _subscription_row(
                row_id=_CONTROL_SUBSCRIPTION_ID,
                owner_id=_CONTROL_OWNER_ID,
                price_minor=_VALID_AMOUNT,
                currency=_VALID_CURRENCY,
            )
        ]
    )
    grant = RecordingGrant()

    # ── Half one: the arithmetic itself refuses it. ──
    with pytest.raises(InvalidAmount):
        split_ninety_ten(amount)

    # ── Half two: so does the settlement path, before issuing a single statement. ──
    with _recording_audit() as audit:
        with pytest.raises(InvalidAmount):
            _settle(
                client,
                reference="pi_inadmissible",
                amount_minor=amount,
                currency=_VALID_CURRENCY,
                subscription_id=_CONTROL_SUBSCRIPTION_ID,
                grant=grant,
            )

        assert client.settlements == [], (
            f"an inadmissible amount {amount!r} wrote a Settlement_Record: {client.settlements}"
        )
        assert not client.wrote_anything(), (
            f"an inadmissible amount {amount!r} issued a write: "
            f"{[(s.op, s.table_name) for s in client.statements]}"
        )
        assert client.statements == [], (
            f"an inadmissible amount {amount!r} reached the Persistence_Layer at all: "
            f"{[(s.op, s.table_name) for s in client.statements]}"
        )
        assert client.transitions == [] and client.permissions == []
        assert grant.calls == [], "an inadmissible amount granted an entitlement"
        assert audit.acts == [], (
            f"an inadmissible amount {amount!r} wrote an audit line before being refused: "
            f"{audit.action_names()}"
        )

        # Nothing was truncated or rounded into existence: no row carries any integer near the
        # refused value. Asserted explicitly, because "the ledger is empty" and "the ledger holds
        # int(19.99)" are the two outcomes this property distinguishes.
        assert _ledger_rows(client) == []

        # ── The control: the same call shape, with an admissible amount, records exactly one
        #    row. Without this the assertions above would also pass against a double that
        #    accepted nothing. ──
        control = _settle(
            client,
            reference="pi_admissible",
            amount_minor=_VALID_AMOUNT,
            currency=_VALID_CURRENCY,
            subscription_id=_CONTROL_SUBSCRIPTION_ID,
            grant=grant,
        )

    assert control.outcome is SettlementOutcome.RECORDED
    recorded = _ledger_rows(client)
    assert len(recorded) == 1, f"the control settlement wrote {len(recorded)} rows"
    assert int(recorded[0]["amount_minor"]) == _VALID_AMOUNT
    assert control.owner_share_minor == _owner_share_oracle(_VALID_AMOUNT)
    assert (
        control.owner_share_minor + control.platform_fee_minor == _VALID_AMOUNT
    ), "the control settlement's split does not conserve"
