"""
backend_app/backend/marketplace/checkout_service.py - the one Marketplace checkout path.

Spec: marketplace-subscriptions-paper-trading task 18.1. ``design.md`` -> "Root-cause fix 1 -
``create_marketplace_checkout``". Requirements 9.1, 9.2, 9.3, 9.4, 9.10, 9.12, 9.13, 10.1,
10.3, 24.6.

Exposes
-------
PROVIDER_DEADLINE_SECONDS   30 - Requirement 9.13's provider deadline, written once
PROVIDER_FOR_CURRENCY       ``INR -> razorpay``, everything else -> ``stripe`` - the same
                            routing ``routers/billing.py`` already applies (Requirement 9.1)
CHECKOUT_LISTING_SELECT     the explicit Listing projection this path reads
CHECKOUT_SUBSCRIPTION_SELECT the explicit Subscription projection this path reads
ProviderSession             the frozen value type a provider factory returns
CheckoutResult              the frozen value type :func:`create_checkout` returns
CheckoutReadFailed          a Persistence_Layer read did not complete - re-raised as
                            ``MARKETPLACE_READ_FAILED``
create_checkout(...)        the first-purchase entry point; the router maps its result onto HTTP
create_renewal_checkout(...) the renewal entry point (task 19.3) - the SAME provider path, the
                            SAME webhook metadata, and **no write at all**: the transition into
                            ``ACTIVE`` and the new expiry belong to ``settlement_service.settle``
                            (Requirements 11.6, 11.14, 11.16)

WHAT THIS MODULE REPLACES
-------------------------
``routers/library.py``'s ``create_marketplace_checkout`` carried five separate defects, each of
which this module removes at its root rather than at its symptom:

1. ``strat = resp.data[0]`` on a ``.single()`` response. ``.single()`` returns a **dict**, so
   indexing it with ``0`` raised ``KeyError: 0`` and the endpoint never completed for any
   Listing. Here the Listing read returns rows through :func:`_rows`, which normalises the
   ``.single()`` dict, the ``{"data": ...}`` envelope and the bare list into one list shape, so
   there is no index to get wrong.
2. ``int(float(price) * 100)``. A ``19.99`` Listing charged **1998**, because ``19.99`` is not
   nineteen and ninety-nine hundredths in binary and ``int()`` truncates the ``1998.9999…``
   that results. Here the amount is :func:`money.amount_for_listing`, which returns the stored
   ``price_minor`` ``int`` unchanged. **No ``float`` appears anywhere in this module**
   (Requirements 9.2, 10.3).
3. ``"expires_at": None`` on the ``PENDING`` insert. ``check_deployment_permission`` carries a
   branch reading ``else: # No expiry date means perpetual subscription``, so any row that
   reached ``active`` with a null expiry granted **permanent** access to a monthly Listing.
   This module **omits the column entirely** rather than writing ``NULL`` into it - see
   "WHY ``expires_at`` IS OMITTED AND NOT NULLED" below.
4. ``STRIPE_SECRET_KEY`` read with a ``"sk_test_dummy"`` default, and ``stripe`` / ``razorpay``
   imported inline in the handler. Credential validation now goes through the one place that
   already refuses a test credential on a production path -
   ``routers.billing._validate_keys`` - and the SDK imports live here, so the handler imports
   neither SDK and reads neither key.
5. ``svc.table("library_subscriptions").delete().eq("id", sub_id)`` inside a bare
   ``except: pass``. A failed provider call **deleted the audit trail of the attempt**, which is
   exactly what Requirement 9.4 forbids. Here the row is ``UPDATE``d to ``payment_failed`` with
   a ``failure_cause`` and a ``failed_at`` and is **never deleted**.

THE ORDERING, AND WHY IT IS THE POINT (Requirements 9.3, 9.4)
------------------------------------------------------------
The persisted ``PENDING`` row is committed **before** any provider session is requested. That
ordering is not a preference: a provider session created before the row exists is a payment the
system has no record of, and a webhook arriving for it has nothing to correlate against. The
sequence is therefore fixed and, because it is expressed as two separate function calls with a
value passed between them, it is *observable*:

    _read_listing → _read_caller_subscription → money split → _persist_pending  (COMMITTED)
                                                                     ↓
                                                       _request_provider_session
                                                                     ↓
                                            _record_provider_reference  |  _record_failure

``supabase-py`` gives no explicit transaction handle, so "one transaction" is one statement:
the single ``insert()`` (or, on a retry of the caller's own failed attempt, the single
``update()``) that writes every column of the ``PENDING`` row at once. It returns only once the
statement has committed, so there is no window in which a partially populated row is visible,
and no second write is needed to complete it. ``chk_ls_split_conserved`` and
``chk_ls_active_has_period`` are evaluated against that one row image.

WHY ``expires_at`` IS OMITTED AND NOT NULLED (Requirement 9.3, 24.6)
-------------------------------------------------------------------
``PENDING`` means "no period has begun". There is no expiry to record, and the two ways of
saying so are not equivalent:

* Writing ``NULL`` puts a value in the column that ``check_deployment_permission`` reads as
  *perpetual*. One later ``UPDATE ... SET status='active'`` that forgets the period columns then
  produces an unexpiring paid subscription. That is a privilege defect, not a cosmetic one.
* Omitting the column leaves it at its default and, more importantly, leaves
  ``chk_ls_active_has_period`` - ``status <> 'active' OR (period_start IS NOT NULL AND
  period_expiry IS NOT NULL)`` - as the thing that makes an ``ACTIVE`` row without a period
  **unrepresentable**. The database refuses the row rather than the application remembering not
  to write it.

So neither ``expires_at`` nor ``period_start`` nor ``period_expiry`` appears in the payload at
all. :data:`OMITTED_ON_PENDING` names them and :func:`_pending_payload` asserts none of them
reached the payload, so a future edit that adds one back fails here.

``price_paid`` is omitted for the neighbouring reason: it is the legacy ``NUMERIC(10,2)``
column, mirrored *from* ``price_minor``, and populating it from the authoritative integer would
mean choosing a decimal representation on a write path this module keeps free of ``Decimal`` and
``float`` alike. ``price_minor`` is the amount; ``subscription_tier`` is likewise omitted so the
column's own ``'free'`` default applies rather than this path inventing a tier value that
``valid_subscription_tier`` would refuse (the old handler's ``"standard"`` was not one of the
three permitted values).

WHAT WAS REUSED FROM ``routers/billing.py``, AND WHAT HAD TO BE ADDED
--------------------------------------------------------------------
Requirement 9.1 permits exactly one Billing_Integration, one webhook handler per provider and
one checkout construction path. Reused, by import rather than by copy:

* ``billing._validate_keys(provider)`` - the credential gate. It is the single place that
  refuses a missing key, the ``"sk_test_dummy"`` / ``"rzp_test_dummy"`` placeholders and a
  non-``sk_live_`` / non-``rzp_live_`` credential on a production path. Reusing it is what
  removes defect 4 above; writing a second validator here would have re-created the very
  default this task exists to delete. It raises ``fastapi.HTTPException``, which is a *router*
  outcome, so :func:`_provider_credentials` translates it into
  :class:`ProviderSessionFailed` - the service's own defined outcome - and the row lands in
  ``payment_failed`` like any other pre-session failure.
* The currency → provider routing (``INR`` → Razorpay, everything else → Stripe), lifted into
  :data:`PROVIDER_FOR_CURRENCY` so the two call sites read one table instead of two ``if``
  ladders.
* The webhook metadata contract. ``billing._apply_billing_entitlement`` dispatches on
  ``item_key.startswith("marketplace_")`` and ``_apply_marketplace_entitlement`` reads
  ``metadata["subscription_id"]``; the Razorpay half reads ``notes["item"]``. Both spellings are
  reproduced exactly, so the **existing** webhook handlers activate these subscriptions. No
  second webhook handler is added.

Had to be added, and why:

* The session parameters themselves. ``billing.create_payment_link`` is bound to a plan
  purchase: it takes a ``CheckoutRequest``, resolves an ``item_key`` against
  ``VALID_ITEM_KEYS``, runs ``FXService`` localisation, applies a profile discount and opens the
  Stripe session in ``mode="subscription"`` with a recurring price. A marketplace checkout has
  none of those: the amount is the Listing's own ``price_minor`` in the Listing's own currency,
  there is no FX conversion (Requirement 10.7 forbids mixing currencies), no discount, and the
  mode is a one-off ``"payment"``. Calling it would have meant either widening it with
  marketplace branches or fabricating a plan ``item_key`` for it; both are worse than the small
  amount of session-parameter construction here. This is the *same* provider clients and the
  *same* webhook - only the line item differs, so it is not a second integration.
* The 30-second deadline and the ``payment_failed`` bookkeeping. ``billing`` has neither; they
  are Requirements 9.13 and 9.4, which apply to this path.

WHY A FAILED READ IS NOT AN ANSWER (Requirements 1.5, 1.7, 30.5)
----------------------------------------------------------------
Neither read may be swallowed. A failed Listing read answered as "not purchasable" would tell a
buyer a published Listing is unavailable; a failed Subscription read answered as "no
subscription" would let a subscriber be charged twice. Each read is wrapped in exactly one
``except`` that **re-raises** :class:`CheckoutReadFailed`, and a response carrying a non-empty
``error`` envelope raises the same rather than being read as "no rows". There is no broad
``except`` in this module that returns a value; every one either re-raises or converts to a
declared error outcome.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Sequence,
    Union,
)

from backend_app.backend.marketplace import money
from backend_app.backend.marketplace.errors import (
    MARKETPLACE_CHECKOUT_UNAVAILABLE,
    MARKETPLACE_LISTING_NOT_PURCHASABLE,
    MARKETPLACE_OWN_LISTING,
    MARKETPLACE_READ_FAILED,
    NOT_FOUND,
    MarketplaceError,
)
from backend_app.backend.marketplace.settlement_service import ELIGIBLE_FOR_ACTIVATION
from backend_app.backend.marketplace.submission_state import PUBLIC_STATES
from backend_app.backend.marketplace.subscription_state import (
    STATUS_TEXT_FOR_STATE,
    SubscriptionState,
)

logger = logging.getLogger("MarketplaceCheckout")

__all__ = [
    "PROVIDER_DEADLINE_SECONDS",
    "PROVIDER_FOR_CURRENCY",
    "SUPPORTED_PROVIDERS",
    "OMITTED_ON_PENDING",
    "CHECKOUT_LISTING_SELECT",
    "CHECKOUT_SUBSCRIPTION_SELECT",
    "RENEWAL_SUBSCRIPTION_SELECT",
    "RENEWABLE_STATUSES",
    "ProviderSession",
    "CheckoutResult",
    "CheckoutReadFailed",
    "CheckoutWriteFailed",
    "ProviderSessionFailed",
    "create_checkout",
    "create_renewal_checkout",
]


# ══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════

#: Requirement 9.13's deadline, in seconds. Written once, here, so the value the code enforces
#: and the value the regression test asserts are the same literal.
PROVIDER_DEADLINE_SECONDS = 30

#: The persisted ``marketplace_settlements.provider`` / ``library_subscriptions.provider``
#: spellings. ``chk_settlement_provider`` pins the column to exactly these two, which is
#: Requirement 9.1's "no third provider" written where a handler cannot talk past it.
SUPPORTED_PROVIDERS: FrozenSet[str] = frozenset({"stripe", "razorpay"})

#: Currency → provider, the same routing ``billing.create_payment_link`` already applies: INR
#: settles through Razorpay, every other supported currency through Stripe. A currency absent
#: from :data:`money.MINOR_UNIT_EXPONENT` never reaches here - the amount read refuses it first.
PROVIDER_FOR_CURRENCY: Mapping[str, str] = MappingProxyType(
    {"INR": "razorpay", "USD": "stripe"}
)

#: The columns that must NOT appear in the ``PENDING`` payload, and the reason each is excluded.
#: Asserted by :func:`_pending_payload`, so this is a check rather than a comment.
#:
#: * ``expires_at`` / ``period_expiry`` / ``period_start`` - a ``PENDING`` row has no period.
#:   Writing ``NULL`` into ``expires_at`` is read as *perpetual* by
#:   ``check_deployment_permission``; omitting it leaves ``chk_ls_active_has_period`` to make an
#:   ``ACTIVE`` row without a period unrepresentable (Requirement 9.3).
#: * ``price_paid`` - the legacy ``NUMERIC(10,2)`` mirror of ``price_minor``. Populating it here
#:   would put a non-integer money value on this path (Requirement 10.3).
#: * ``subscription_tier`` - ``valid_subscription_tier`` admits only ``free``/``pro``/``elite``;
#:   the column's own default applies rather than this path inventing a value.
#: * ``status`` on a *renewal-of-own-failed-attempt* update is not listed here because the
#:   status IS written - it is the one column that makes the row ``PENDING``.
OMITTED_ON_PENDING: FrozenSet[str] = frozenset(
    {
        "expires_at",
        "period_expiry",
        "period_start",
        "price_paid",
        "subscription_tier",
    }
)

#: The explicit Listing projection. No ``select("*")``: the columns this path needs are named,
#: and the set is bound by the ``checkout`` entry of this package's ``COLUMN_CONTRACT`` and
#: asserted a subset of it by ``tests/test_marketplace_paper_schema_contract.py``.
#:
#: ``marketplace_submissions(submission_state)`` is a PostgREST embed: the Submission_State that
#: decides purchasability travels with the Listing rather than costing a second round trip.
CHECKOUT_LISTING_SELECT = (
    "id,name,author_id,price_minor,currency,is_active,"
    "marketplace_submissions(submission_state)"
)

#: The caller's own Subscription row. Read as its own statement rather than as an embed so the
#: ``user_id`` predicate is a *server-side* filter: a row belonging to another purchaser is
#: never returned, so it cannot participate in the "already subscribed" decision even in memory
#: (Requirement 21.1).
CHECKOUT_SUBSCRIPTION_SELECT = "id,user_id,status"

#: The renewal path's Subscription projection (task 19.3). ``library_id`` is added because a
#: renewal is addressed by its *Subscription* id and has to find its own Listing to read the
#: renewal amount from; ``status`` decides renewability. Nothing else is read, and in particular
#: no period column is: :func:`create_renewal_checkout` writes nothing and therefore has no
#: reason to know when the current period ends - ``settlement_service._apply_transition`` reads
#: the stored ``period_expiry`` itself when the payment confirms.
RENEWAL_SUBSCRIPTION_SELECT = "id,user_id,library_id,status"

#: ``library_subscriptions.status`` spellings, resolved through the one enum → column mapping
#: rather than re-spelled as literals here.
_STATUS_PENDING = STATUS_TEXT_FOR_STATE[SubscriptionState.PENDING]
_STATUS_ACTIVE = STATUS_TEXT_FOR_STATE[SubscriptionState.ACTIVE]
_STATUS_PAYMENT_FAILED = STATUS_TEXT_FOR_STATE[SubscriptionState.PAYMENT_FAILED]

#: The states of the caller's *own* existing row that this path may write to.
#:
#: ``pending`` is a re-issue of a session for an attempt that never completed - the status does
#: not change, so ``trg_subscription_transition_guard`` sees a no-op write and lets it through.
#: ``payment_failed`` → ``pending`` is one of Requirement 11.2's twelve permitted edges.
#:
#: Every other state is refused. ``expired`` and ``cancelled`` transition only to ``active``,
#: and reaching ``active`` needs a Settlement_Record: a *renewal* is therefore a settlement
#: operation on the existing row, not a new ``PENDING`` insert, and it is task 19's
#: ("settlement, and renewal that requires payment"). Building a renewal branch here would be
#: the second checkout construction path Requirement 9.1 forbids, so this path refuses with
#: ``MARKETPLACE_LISTING_NOT_PURCHASABLE`` and names the reason in ``details`` instead of
#: guessing at a transition the guard would reject anyway.
_REUSABLE_STATUSES: FrozenSet[str] = frozenset({_STATUS_PENDING, _STATUS_PAYMENT_FAILED})

#: The statuses :func:`create_renewal_checkout` will sell a renewal for (task 19.3).
#:
#: Derived from ``settlement_service.ELIGIBLE_FOR_ACTIVATION`` rather than re-enumerated, because
#: that frozenset is the set of statuses a confirmed payment can actually move to ``active`` and
#: ``tests/test_submission_state_agreement.py`` asserts it equals the database's own sources of
#: ``-> active``. Selling a renewal for a status the guard would then refuse is a purchaser
#: charged with no access, which is the shape of the defect this whole task exists to remove, so
#: the two sets are one set.
#:
#: Two deliberate adjustments, both visible here rather than folded into a literal:
#:
#: * ``active`` is ADDED. It is not a transition and so is absent from the transition table, but
#:   ``settlement_service._apply_transition`` handles ``active -> active`` as a renewal in place,
#:   extending from the stored ``period_expiry`` (task 19.2). An ``ACTIVE`` subscriber renewing
#:   early buys the next month; refusing them would be refusing a payment the settlement path
#:   knows how to apply.
#: * ``pending`` is REMOVED. A ``PENDING`` row has no period to extend - its first payment has
#:   never confirmed - so it is an unfinished *checkout*, not a renewal, and belongs on
#:   ``POST /{library_id}/checkout``, whose ``_REUSABLE_STATUSES`` re-issues a session for it.
#:
#: ``refunded`` is in neither set: it is terminal.
RENEWABLE_STATUSES: FrozenSet[str] = (
    frozenset(ELIGIBLE_FOR_ACTIVATION) - {_STATUS_PENDING}
) | {_STATUS_ACTIVE}

#: The longest ``failure_cause`` this module will persist. A provider or driver message is
#: unbounded; the column is not the place to store a full traceback, and the *client* never sees
#: this value at all (``MARKETPLACE_CHECKOUT_UNAVAILABLE``'s catalogue sentence is what the
#: caller gets), so it is truncated rather than reproduced.
_MAX_FAILURE_CAUSE_CHARS = 500


# ══════════════════════════════════════════════════════════════════════════
# THE DEFINED ERROR OUTCOMES
# ══════════════════════════════════════════════════════════════════════════


class CheckoutReadFailed(Exception):
    """A read this path needs did not complete, so there is no purchasability decision.

    Carried separately from :class:`MarketplaceError` so the read failure is distinguishable
    from a refusal at the point it happens; :func:`create_checkout` converts it into
    ``MARKETPLACE_READ_FAILED`` (503) at its boundary. Answering "not purchasable" or "not
    subscribed" for a broken read would be a fabricated fact (Requirements 1.5, 1.7, 30.5).
    """

    wire_code: str = MARKETPLACE_READ_FAILED


class CheckoutWriteFailed(Exception):
    """The ``PENDING`` row could not be written, so no provider session is requested.

    This is the ordering of Requirement 9.3 held to: with no committed row there is nothing for
    a webhook to correlate against, so the attempt stops here rather than creating a payment the
    system has no record of.
    """

    wire_code: str = MARKETPLACE_READ_FAILED


class ProviderSessionFailed(Exception):
    """The provider session was not created: it raised, or it exceeded the deadline.

    Carries the ``cause`` that is persisted to ``failure_cause`` - an internal string that stays
    in the database and never reaches a client body, where the catalogue's own sentence for
    ``MARKETPLACE_CHECKOUT_UNAVAILABLE`` is what the caller reads (Requirement 22.9).
    """

    def __init__(self, cause: str, *, timed_out: bool = False) -> None:
        self.cause = cause
        self.timed_out = timed_out
        super().__init__(cause)


# ══════════════════════════════════════════════════════════════════════════
# THE VALUE TYPES
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ProviderSession:
    """What a provider factory returns: the reference to record, and the caller's redirect.

    Frozen because it is evidence of a completed side effect at the provider. ``reference`` is
    the value persisted to ``library_subscriptions.provider_reference`` and the value the
    webhook correlates on, so it is required; ``checkout_url`` is absent for the Razorpay order
    flow, where the browser opens the checkout with the order id and the publishable key
    instead.
    """

    provider: str
    reference: str
    checkout_url: Optional[str] = None
    #: Provider-specific members the router adds to its response body verbatim (Razorpay's
    #: ``razorpay_key`` and ``amount``, Stripe's nothing). Never a secret: the Razorpay key id
    #: is the publishable half of the pair and is already returned by ``billing``.
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"{self.provider!r} is not one of {sorted(SUPPORTED_PROVIDERS)}; "
                "Requirement 9.1 permits no third payment provider"
            )
        if not self.reference:
            raise ValueError(
                "a provider session with no reference cannot be correlated to a webhook, "
                "so it cannot be recorded"
            )


@dataclass(frozen=True)
class CheckoutResult:
    """The outcome of one successful checkout construction.

    The router turns this into an HTTP body. Every money value on it is an ``int`` number of
    Minor_Units - there is no ``float`` and no ``Decimal`` field here, so a response cannot
    reintroduce the inexactness Requirement 10.3 forbids.
    """

    subscription_id: str
    listing_id: str
    provider: str
    provider_reference: str
    amount_minor: int
    currency: str
    owner_share_minor: int
    platform_fee_minor: int
    checkout_url: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=dict)


#: A provider factory: given the resolved checkout facts, create the session at the provider and
#: return the reference. May be a plain callable (run in a worker thread, because both SDKs are
#: blocking) or a coroutine function (awaited directly). Injected so the deadline, the failure
#: path and the ordering are all testable without a network call or an SDK installed.
ProviderSessionFactory = Callable[
    ["CheckoutContext"], Union[ProviderSession, Awaitable[ProviderSession]]
]


@dataclass(frozen=True)
class CheckoutContext:
    """Everything a provider factory needs, and nothing it does not.

    Deliberately carries no Persistence_Layer handle and no caller credentials: a provider
    factory constructs a session and returns a reference, it does not read or write the
    Subscription row. That is what keeps the ordering of Requirement 9.3 enforceable - the row
    is already committed before this value is built, and the factory has no way to touch it.
    """

    provider: str
    subscription_id: str
    listing_id: str
    listing_name: str
    purchaser_id: str
    amount_minor: int
    currency: str


# ══════════════════════════════════════════════════════════════════════════
# THE ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════


async def create_checkout(
    *,
    caller: Any,
    listing_id: Any,
    currency: Any,
    supabase: Any,
    now: Optional[datetime] = None,
    provider_session_factory: Optional[ProviderSessionFactory] = None,
    deadline_seconds: float = PROVIDER_DEADLINE_SECONDS,
) -> CheckoutResult:
    """Create a checkout session for ``caller`` to subscribe to the Listing ``listing_id``.

    The one public entry point. It is a thin boundary over :func:`_create_checkout`: its only
    job is to turn the two Persistence_Layer outcomes into the catalogue's structured error, so
    that every exception leaving this module is either a :class:`MarketplaceError` the router can
    serialise unchanged or a ``ValueError`` about its own arguments. Nothing is swallowed - both
    branches re-raise, chained from the original.

    See :func:`_create_checkout` for the argument and refusal documentation.
    """
    try:
        return await _create_checkout(
            caller=caller,
            listing_id=listing_id,
            currency=currency,
            supabase=supabase,
            now=now,
            provider_session_factory=provider_session_factory,
            deadline_seconds=deadline_seconds,
        )
    except (CheckoutReadFailed, CheckoutWriteFailed) as exc:
        logger.error("marketplace checkout could not complete: %s", exc)
        raise MarketplaceError(
            MARKETPLACE_READ_FAILED,
            http_status=503,
            details={"listing_id": _as_text(listing_id)},
        ) from exc


async def _create_checkout(
    *,
    caller: Any,
    listing_id: Any,
    currency: Any,
    supabase: Any,
    now: Optional[datetime] = None,
    provider_session_factory: Optional[ProviderSessionFactory] = None,
    deadline_seconds: float = PROVIDER_DEADLINE_SECONDS,
) -> CheckoutResult:
    """Create a checkout session for ``caller`` to subscribe to the Listing ``listing_id``.

    The service owns the transaction; the router owns HTTP. Every refusal leaves the
    Persistence_Layer exactly as it found it, and no refusal creates a provider session.

    Args:
        caller: the authenticated server-side identity. Only its ``id`` is read - no identifier
            from a request body, query string or path participates in the ownership or
            subscription decision (Requirements 7.7, 21.1).
        listing_id: the ``library_strategies.id`` being purchased.
        currency: the ISO 4217 code the caller asked to pay in. It must equal the Listing's own
            ``currency``: the charged amount is the Listing's ``price_minor`` in the Listing's
            currency, and Requirement 10.7 admits no cross-currency conversion.
        supabase: the injected Persistence_Layer handle (a service-role client at the route).
        now: the instant recorded as the row's creation timestamp. Passed in so the ordering and
            the timestamps are deterministic under test; defaults to the current UTC instant.
        provider_session_factory: the provider session constructor. Defaults to
            :func:`default_provider_session_factory`, which builds the Stripe or Razorpay client
            through ``billing._validate_keys``.
        deadline_seconds: the provider deadline. Defaults to :data:`PROVIDER_DEADLINE_SECONDS`
            (30); overridden only by tests, which cannot afford to wait thirty seconds to prove
            the deadline exists.

    Returns:
        A :class:`CheckoutResult` whose ``amount_minor`` is exactly the Listing's stored
        ``price_minor``.

    Raises:
        MarketplaceError: ``MARKETPLACE_OWN_LISTING`` (400) when the caller owns the Listing;
            ``MARKETPLACE_LISTING_NOT_PURCHASABLE`` (409) when the Submission is not
            ``PUBLISHED``, when the caller already holds an ``ACTIVE`` Subscription, when the
            Listing carries no usable ``price_minor``, or when the requested currency is not the
            Listing's; ``NOT_FOUND`` (404) when no such Listing exists;
            ``MARKETPLACE_READ_FAILED`` (503) when a read or the ``PENDING`` write did not
            complete; ``MARKETPLACE_CHECKOUT_UNAVAILABLE`` (502) when the provider session
            failed or exceeded its deadline.
    """
    caller_id = _require_text(_get(caller, "id"), "caller id")
    listing_key = _require_text(listing_id, "listing_id")
    requested_currency = _normalise_currency(currency)
    instant = _coerce_instant(now)

    # ── 1. The refusals. No provider session and no Subscription row is created by any of
    #       them (Requirement 9.10). ──
    listing = _read_listing(supabase, listing_key)
    if listing is None:
        # The single cross-tenant not-found shape: a Listing that does not exist and one the
        # caller may not see are indistinguishable (Requirement 21.4).
        raise MarketplaceError(NOT_FOUND, details={"listing_id": listing_key})

    owner_id = _as_text(_get(listing, "author_id"))

    # Ownership first. It is decidable from the caller's own relationship to the row, discloses
    # nothing the caller does not already know, and is the more precise answer for an owner who
    # would otherwise be told their own listing is "not purchasable" (Requirement 9.10).
    if _same_id(owner_id, caller_id):
        raise MarketplaceError(MARKETPLACE_OWN_LISTING, details={"listing_id": listing_key})

    if not _is_published(listing):
        raise _not_purchasable(listing_key, reason="not_published")

    listing_currency = _normalise_currency(_get(listing, "currency"))
    if requested_currency != listing_currency:
        # No FX on this path: the amount is the Listing's own integer in the Listing's own
        # currency, and Requirement 10.7 forbids combining currencies.
        raise _not_purchasable(
            listing_key,
            reason="currency_not_offered",
            requested_currency=requested_currency,
            listing_currency=listing_currency,
        )

    provider = PROVIDER_FOR_CURRENCY.get(listing_currency)
    if provider is None:
        raise _not_purchasable(
            listing_key, reason="currency_not_offered", listing_currency=listing_currency
        )

    existing = _read_caller_subscription(supabase, listing_key, caller_id)
    existing_status = _normalise_status(_get(existing, "status")) if existing else None
    if existing_status == _STATUS_ACTIVE:
        raise _not_purchasable(listing_key, reason="already_subscribed")
    if existing is not None and existing_status not in _REUSABLE_STATUSES:
        # A lapsed, cancelled, refunded or suspended Subscription is a RENEWAL, and a renewal
        # reaches ``active`` only through a Settlement_Record on the existing row. That is task
        # 19's path, not a second PENDING insert here (Requirement 9.1).
        raise _not_purchasable(
            listing_key, reason="renewal_required", subscription_status=existing_status
        )

    # ── 2. The amount, exact. ``price_minor`` unchanged, then the 90/10 split - both integer
    #       arithmetic, no ``float`` (Requirements 9.2, 10.1, 10.3). ──
    try:
        amount_minor = money.amount_for_listing(listing)
    except money.InvalidAmount as exc:
        # A Listing with no ``price_minor``, a NULL one, or one outside the admissible domain is
        # not purchasable. It is NOT "free": the old handler's ``if not strat.get("price")``
        # conflated an unpriced Listing with a free one, and the ``price`` column it read is the
        # legacy mirror rather than the authority.
        logger.warning(
            "checkout refused: listing %s carries no usable price_minor: %s", listing_key, exc
        )
        raise _not_purchasable(listing_key, reason="no_price") from exc

    owner_share_minor, platform_fee_minor = money.split_ninety_ten(amount_minor)

    # ── 3. One statement, COMMITTED, before any provider session is requested (Req 9.3). ──
    subscription_id = _persist_pending(
        supabase,
        existing=existing,
        listing_id=listing_key,
        purchaser_id=caller_id,
        owner_id=owner_id,
        amount_minor=amount_minor,
        currency=listing_currency,
        owner_share_minor=owner_share_minor,
        platform_fee_minor=platform_fee_minor,
        provider=provider,
        instant=instant,
    )

    # ── 4. Only now is the provider asked for a session, under a 30-second deadline (9.13). ──
    context = CheckoutContext(
        provider=provider,
        subscription_id=subscription_id,
        listing_id=listing_key,
        listing_name=_as_text(_get(listing, "name")) or "Strategy subscription",
        purchaser_id=caller_id,
        amount_minor=amount_minor,
        currency=listing_currency,
    )
    factory = provider_session_factory or default_provider_session_factory

    try:
        session = await _request_provider_session(factory, context, deadline_seconds)
    except ProviderSessionFailed as exc:
        # The row is UPDATEd, never deleted (Requirement 9.4). The delete-inside-``except: pass``
        # this replaces destroyed the only record that the attempt had ever been made.
        _record_failure(supabase, subscription_id, cause=exc.cause, instant=_utc_now())
        raise MarketplaceError(
            MARKETPLACE_CHECKOUT_UNAVAILABLE,
            details={
                "listing_id": listing_key,
                "subscription_id": subscription_id,
                "timed_out": exc.timed_out,
            },
        ) from exc

    # ── 5. Record the handshake against the PENDING row before returning (Req 9.4, 9.12). ──
    try:
        _record_provider_reference(
            supabase,
            subscription_id,
            provider=session.provider,
            provider_reference=session.reference,
            instant=_utc_now(),
        )
    except CheckoutWriteFailed as exc:
        # The session exists at the provider but this row cannot be correlated to it, so the
        # webhook would have nothing to activate. That is a failed checkout, and it takes the
        # same PAYMENT_FAILED path - the row still is not deleted.
        _record_failure(
            supabase,
            subscription_id,
            cause=f"provider reference could not be recorded: {exc}",
            instant=_utc_now(),
        )
        raise MarketplaceError(
            MARKETPLACE_CHECKOUT_UNAVAILABLE,
            details={
                "listing_id": listing_key,
                "subscription_id": subscription_id,
                "timed_out": False,
            },
        ) from exc

    return CheckoutResult(
        subscription_id=subscription_id,
        listing_id=listing_key,
        provider=session.provider,
        provider_reference=session.reference,
        amount_minor=amount_minor,
        currency=listing_currency,
        owner_share_minor=owner_share_minor,
        platform_fee_minor=platform_fee_minor,
        checkout_url=session.checkout_url,
        extra=dict(session.extra),
    )


# ══════════════════════════════════════════════════════════════════════════
# THE RENEWAL (task 19.3 - Requirements 11.6, 11.14, 11.16)
# ══════════════════════════════════════════════════════════════════════════


async def create_renewal_checkout(
    *,
    caller: Any,
    subscription_id: Any,
    supabase: Any,
    provider_session_factory: Optional[ProviderSessionFactory] = None,
    deadline_seconds: float = PROVIDER_DEADLINE_SECONDS,
) -> CheckoutResult:
    """Create a provider session for the renewal of ``subscription_id``. **Writes nothing.**

    WHAT THIS REPLACES, AND WHY IT IS A PRIVILEGE FIX
    -------------------------------------------------
    ``routers/library.renew_subscription`` used to answer a bare
    ``POST /api/library/subscriptions/{sub_id}/renew`` by doing this::

        svc.table("library_subscriptions").update({
            "status": "active", "cancelled_at": None, "expires_at": None
        }).eq("id", sub_uid).in_("status", ["cancelled", "expired"]).execute()
        ...
        grant_deployment_permission(user_id, lib_id, "subscription", sub_uid)

    No provider was contacted, no amount was computed, no Settlement_Record was written - and
    ``expires_at`` was set to **NULL**, which ``check_deployment_permission`` read as a perpetual
    entitlement. Any purchaser who had ever paid once, then cancelled, could convert their lapsed
    Subscription into unlimited free access with one unpriced POST. That is what Requirement
    11.16 orders removed and Requirements 11.6 and 11.14 forbid.

    WHAT IT DOES INSTEAD
    --------------------
    Exactly what a renewal is: it prices the next period and asks the provider for a session for
    it. **No statement is issued against ``library_subscriptions`` at all** - not the status, not
    the period, not ``cancelled_at``, not even a ``provider_reference``. The transition into
    ``ACTIVE`` and the new expiry happen in one place only, ``settlement_service.settle``, on a
    confirmed payment, in the same transaction as the Settlement_Record the database's
    ``trg_subscription_transition_guard`` insists on.

    WHY NOT EVEN A ``provider_reference`` (the difference from :func:`create_checkout`)
    ---------------------------------------------------------------------------------
    The initial checkout writes a ``PENDING`` row first because there is otherwise nothing for a
    webhook to correlate against (Requirement 9.3). A renewal has no such gap: the row already
    exists, and the payment finds it through ``metadata["subscription_id"]``, which
    :func:`_settlement_metadata` puts on the Stripe session, the Stripe PaymentIntent and the
    Razorpay order notes alike, and which ``billing._apply_marketplace_entitlement`` already
    reads. Writing the new reference over the row would also *overwrite the reference of the
    payment that bought the current period* - the value
    ``settlement_service.find_settled_payment`` correlates a later refund by. So the correct
    number of writes here is zero, and "changes no stored state" is a property of this function
    rather than a promise about it.

    That is also why a provider failure does **not** land the row in ``payment_failed`` the way
    :func:`create_checkout`'s does. On this path the row is very often ``ACTIVE`` - a subscriber
    renewing before their period ends - and demoting a live, paid-for entitlement because a
    checkout session could not be opened would end access somebody has already paid for. The
    caller is answered ``MARKETPLACE_CHECKOUT_UNAVAILABLE`` and the Subscription is untouched.

    Args:
        caller: the authenticated server-side identity. Only its ``id`` is read, and it is a
            *predicate on the read*, so another purchaser's Subscription is never returned and
            cannot be renewed by its id (Requirements 7.7, 21.1, 21.4).
        subscription_id: the ``library_subscriptions.id`` being renewed.
        supabase: the injected Persistence_Layer handle.
        provider_session_factory: the provider session constructor; defaults to
            :func:`default_provider_session_factory`.
        deadline_seconds: Requirement 9.13's deadline, defaulting to
            :data:`PROVIDER_DEADLINE_SECONDS`.

    Returns:
        A :class:`CheckoutResult` carrying the **existing** ``subscription_id`` and the Listing's
        current ``price_minor`` as ``amount_minor``.

    Raises:
        MarketplaceError: ``NOT_FOUND`` (404) when there is no such Subscription for this caller
            - the same body a Subscription belonging to somebody else produces;
            ``MARKETPLACE_LISTING_NOT_PURCHASABLE`` (409) when the Subscription's status is not
            renewable, when the Listing is no longer published, or when it carries no usable
            ``price_minor``; ``MARKETPLACE_READ_FAILED`` (503) when a read did not complete;
            ``MARKETPLACE_CHECKOUT_UNAVAILABLE`` (502) when the provider session failed or
            exceeded its deadline.
    """
    caller_id = _require_text(_get(caller, "id"), "caller id")
    subscription_key = _require_text(subscription_id, "subscription_id")

    try:
        subscription = _read_renewal_subscription(supabase, subscription_key, caller_id)
    except CheckoutReadFailed as exc:
        # A read that did not complete is not "no such subscription": answering 404 for it would
        # tell a paying subscriber their Subscription is gone (Requirements 1.5, 1.7, 30.5).
        logger.error("marketplace renewal could not complete: %s", exc)
        raise MarketplaceError(
            MARKETPLACE_READ_FAILED,
            http_status=503,
            details={"subscription_id": subscription_key},
        ) from exc

    if subscription is None:
        raise MarketplaceError(NOT_FOUND, details={"subscription_id": subscription_key})

    status = _normalise_status(_get(subscription, "status"))
    if status not in RENEWABLE_STATUSES:
        raise MarketplaceError(
            MARKETPLACE_LISTING_NOT_PURCHASABLE,
            details={
                "subscription_id": subscription_key,
                "reason": "not_renewable",
                "subscription_status": status,
            },
        )

    listing_key = _as_text(_get(subscription, "library_id"))
    if not listing_key:
        # A Subscription with no Listing cannot be priced. It is a data defect, not a refusal the
        # caller can act on, so it is not reported as "not purchasable" with a guessed amount.
        raise MarketplaceError(
            MARKETPLACE_READ_FAILED,
            http_status=503,
            details={"subscription_id": subscription_key},
        )

    try:
        listing = _read_listing(supabase, listing_key)
    except CheckoutReadFailed as exc:
        logger.error("marketplace renewal could not read its listing: %s", exc)
        raise MarketplaceError(
            MARKETPLACE_READ_FAILED,
            http_status=503,
            details={"subscription_id": subscription_key},
        ) from exc

    if listing is None:
        raise MarketplaceError(NOT_FOUND, details={"listing_id": listing_key})

    if not _is_published(listing):
        # A withdrawn or suspended Listing is not sold another month. The Entitlement_Resolver
        # would have nothing to admit the renewed period against, so taking the payment would be
        # charging for access that cannot be granted.
        raise _not_purchasable(listing_key, reason="not_published")

    listing_currency = _normalise_currency(_get(listing, "currency"))
    provider = PROVIDER_FOR_CURRENCY.get(listing_currency)
    if provider is None:
        raise _not_purchasable(
            listing_key, reason="currency_not_offered", listing_currency=listing_currency
        )

    # The amount is the Listing's stored ``price_minor``, verbatim, through the same one function
    # the initial checkout uses. No ``float``, no re-scaling, no second arithmetic (Req 9.2, 10.3).
    try:
        amount_minor = money.amount_for_listing(listing)
    except money.InvalidAmount as exc:
        logger.warning(
            "renewal refused: listing %s carries no usable price_minor: %s", listing_key, exc
        )
        raise _not_purchasable(listing_key, reason="no_price") from exc

    owner_share_minor, platform_fee_minor = money.split_ninety_ten(amount_minor)

    context = CheckoutContext(
        provider=provider,
        subscription_id=subscription_key,
        listing_id=listing_key,
        listing_name=_as_text(_get(listing, "name")) or "Strategy subscription",
        purchaser_id=caller_id,
        amount_minor=amount_minor,
        currency=listing_currency,
    )
    factory = provider_session_factory or default_provider_session_factory

    try:
        session = await _request_provider_session(factory, context, deadline_seconds)
    except ProviderSessionFailed as exc:
        # No ``_record_failure`` here - see the docstring. The Subscription is left exactly as it
        # was found, which for an ``ACTIVE`` row means a paid-for entitlement is not ended by a
        # provider outage.
        raise MarketplaceError(
            MARKETPLACE_CHECKOUT_UNAVAILABLE,
            details={
                "subscription_id": subscription_key,
                "listing_id": listing_key,
                "timed_out": exc.timed_out,
            },
        ) from exc

    return CheckoutResult(
        subscription_id=subscription_key,
        listing_id=listing_key,
        provider=session.provider,
        provider_reference=session.reference,
        amount_minor=amount_minor,
        currency=listing_currency,
        owner_share_minor=owner_share_minor,
        platform_fee_minor=platform_fee_minor,
        checkout_url=session.checkout_url,
        extra=dict(session.extra),
    )


# ══════════════════════════════════════════════════════════════════════════
# THE READS
# ══════════════════════════════════════════════════════════════════════════


def _read_renewal_subscription(
    supabase: Any, subscription_id: str, caller_id: str
) -> Optional[Mapping[str, Any]]:
    """The caller's own Subscription addressed by id, or ``None`` when there is no such row.

    ``user_id`` is a predicate on the statement, not a check after it: a Subscription belonging
    to another purchaser is never returned, so it is indistinguishable from one that does not
    exist and both produce the same ``NOT_FOUND`` body (Requirements 21.1, 21.4).

    Raises:
        CheckoutReadFailed: the read did not complete. Answering ``None`` would tell a paying
            subscriber their Subscription does not exist.
    """
    try:
        response = (
            supabase.table("library_subscriptions")
            .select(RENEWAL_SUBSCRIPTION_SELECT)
            .eq("id", subscription_id)
            .eq("user_id", caller_id)
            .execute()
        )
        rows = _rows(response)
    except CheckoutReadFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome, never swallowed
        raise CheckoutReadFailed(
            f"the subscription read for {subscription_id} did not complete: {exc}"
        ) from exc
    for row in rows:
        row_user = _get(row, "user_id")
        if row_user is None or _same_id(row_user, caller_id):
            return row
    return None


def _read_listing(supabase: Any, listing_id: str) -> Optional[Mapping[str, Any]]:
    """The Listing and its embedded Submission_State, or ``None`` when there is no such row.

    Raises:
        CheckoutReadFailed: the read did not complete. Not ``None``, and not a refusal: a broken
            read answered as "not purchasable" would tell a buyer a published Listing is gone.
    """
    try:
        response = (
            supabase.table("library_strategies")
            .select(CHECKOUT_LISTING_SELECT)
            .eq("id", listing_id)
            .execute()
        )
        rows = _rows(response)
    except CheckoutReadFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome, never swallowed
        raise CheckoutReadFailed(
            f"the listing read for {listing_id} did not complete: {exc}"
        ) from exc
    return rows[0] if rows else None


def _read_caller_subscription(
    supabase: Any, listing_id: str, caller_id: str
) -> Optional[Mapping[str, Any]]:
    """The caller's own Subscription row for this Listing, or ``None``.

    ``library_subscriptions`` carries ``UNIQUE (library_id, user_id)``, so there is at most one.
    Both predicates are server-side, so another purchaser's row is never returned.

    Raises:
        CheckoutReadFailed: the read did not complete. Answering "no subscription" for a broken
            read is what would let an already-subscribed caller be charged a second time
            (Requirement 9.10).
    """
    try:
        response = (
            supabase.table("library_subscriptions")
            .select(CHECKOUT_SUBSCRIPTION_SELECT)
            .eq("library_id", listing_id)
            .eq("user_id", caller_id)
            .execute()
        )
        rows = _rows(response)
    except CheckoutReadFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome, never swallowed
        raise CheckoutReadFailed(
            f"the subscription read for listing {listing_id} did not complete: {exc}"
        ) from exc
    for row in rows:
        row_user = _get(row, "user_id")
        if row_user is None or _same_id(row_user, caller_id):
            return row
    return None


# ══════════════════════════════════════════════════════════════════════════
# THE ONE TRANSACTION (Requirement 9.3)
# ══════════════════════════════════════════════════════════════════════════


def _pending_payload(
    *,
    listing_id: str,
    purchaser_id: str,
    owner_id: Optional[str],
    amount_minor: int,
    currency: str,
    owner_share_minor: int,
    platform_fee_minor: int,
    provider: str,
    instant: datetime,
) -> Dict[str, Any]:
    """Every column of the ``PENDING`` row, and only those columns.

    The closing assertion is the enforcement of :data:`OMITTED_ON_PENDING`: ``expires_at``,
    ``period_start``, ``period_expiry``, ``price_paid`` and ``subscription_tier`` must be absent
    from the payload rather than present-and-``NULL``. A future edit that adds ``"expires_at":
    None`` back - the exact line that made a null-expiry row grant perpetual access - fails
    here, in this function, rather than in production.
    """
    payload: Dict[str, Any] = {
        "library_id": listing_id,
        "user_id": purchaser_id,
        "owner_id": owner_id,
        "price_minor": amount_minor,
        "currency": currency,
        "owner_share_minor": owner_share_minor,
        "platform_fee_minor": platform_fee_minor,
        "status": _STATUS_PENDING,
        "provider": provider,
        # The creation timestamp. ``started_at`` is NOT NULL in both existing definitions of the
        # table, and ``trg_lib_subs_period_mirror`` only overwrites it when ``period_start`` is
        # non-null - which it is not here - so this value stands as the record of when the
        # attempt was made.
        "started_at": instant.isoformat(),
        # A fresh attempt carries no failure. Clearing these matters on the retry path, where
        # the row being reused may still hold the previous attempt's cause.
        "failure_cause": None,
        "failed_at": None,
        "provider_reference": None,
        "provider_session_at": None,
    }
    leaked = OMITTED_ON_PENDING & set(payload)
    assert not leaked, (
        f"the PENDING payload must OMIT {sorted(leaked)} rather than carry them; a NULL "
        f"expires_at is read as a perpetual subscription by check_deployment_permission, and "
        f"chk_ls_active_has_period is what makes an ACTIVE row without a period unrepresentable"
    )
    return payload


def _persist_pending(
    supabase: Any,
    *,
    existing: Optional[Mapping[str, Any]],
    listing_id: str,
    purchaser_id: str,
    owner_id: Optional[str],
    amount_minor: int,
    currency: str,
    owner_share_minor: int,
    platform_fee_minor: int,
    provider: str,
    instant: datetime,
) -> str:
    """Write the ``PENDING`` row in one committed statement and return its id.

    One statement, not two: every column of the row - the amount, both split components, the
    owner, the provider and the creation timestamp - is written at once, so no partially
    populated row is ever visible and ``chk_ls_split_conserved`` is evaluated against the
    complete image.

    When the caller already has a row for this Listing in ``pending`` or ``payment_failed``, it
    is UPDATEd rather than inserted beside: ``library_subscriptions`` carries
    ``UNIQUE (library_id, user_id)``, so a second insert would raise ``23505``, and reusing the
    row is also what keeps the earlier attempt's history on one row rather than scattered.

    Raises:
        CheckoutWriteFailed: the statement did not complete. No provider session is requested,
            because there would be no row for a webhook to correlate against (Requirement 9.3).
    """
    payload = _pending_payload(
        listing_id=listing_id,
        purchaser_id=purchaser_id,
        owner_id=owner_id,
        amount_minor=amount_minor,
        currency=currency,
        owner_share_minor=owner_share_minor,
        platform_fee_minor=platform_fee_minor,
        provider=provider,
        instant=instant,
    )

    existing_id = _as_text(_get(existing, "id")) if existing else None
    if existing_id:
        try:
            response = (
                supabase.table("library_subscriptions")
                .update(payload)
                .eq("id", existing_id)
                .eq("user_id", purchaser_id)
                .execute()
            )
            _rows(response)
        except CheckoutReadFailed as exc:
            raise CheckoutWriteFailed(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - converted to the defined outcome, not swallowed
            raise CheckoutWriteFailed(
                f"the pending subscription update for {existing_id} did not complete: {exc}"
            ) from exc
        return existing_id

    subscription_id = str(uuid.uuid4())
    payload["id"] = subscription_id
    try:
        response = supabase.table("library_subscriptions").insert(payload).execute()
        _rows(response)
    except CheckoutReadFailed as exc:
        raise CheckoutWriteFailed(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - converted to the defined outcome, not swallowed
        raise CheckoutWriteFailed(
            f"the pending subscription insert for listing {listing_id} did not complete: {exc}"
        ) from exc
    return subscription_id


def _record_provider_reference(
    supabase: Any,
    subscription_id: str,
    *,
    provider: str,
    provider_reference: str,
    instant: datetime,
) -> None:
    """Record the provider handshake against the ``PENDING`` row (Requirements 9.4, 9.12).

    The status is deliberately not touched: the row stays ``pending`` until a confirmed payment
    arrives, and only a Settlement_Record may move it to ``active``
    (``trg_subscription_transition_guard``).

    Raises:
        CheckoutWriteFailed: the update did not complete.
    """
    try:
        response = (
            supabase.table("library_subscriptions")
            .update(
                {
                    "provider": provider,
                    "provider_reference": provider_reference,
                    "provider_session_at": instant.isoformat(),
                }
            )
            .eq("id", subscription_id)
            .execute()
        )
        _rows(response)
    except CheckoutReadFailed as exc:
        raise CheckoutWriteFailed(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - converted to the defined outcome, not swallowed
        raise CheckoutWriteFailed(
            f"the provider reference write for {subscription_id} did not complete: {exc}"
        ) from exc


def _record_failure(
    supabase: Any, subscription_id: str, *, cause: str, instant: datetime
) -> None:
    """Move the row to ``payment_failed`` with a cause and a timestamp. Never deletes it.

    ``pending -> payment_failed`` is one of Requirement 11.2's twelve permitted edges, so
    ``trg_subscription_transition_guard`` admits it, and the row remains as the record that the
    attempt was made and how it ended (Requirement 9.4).

    A failure to *write the failure* is logged and swallowed here, and this is the one place in
    this module that swallows anything. It is deliberate and bounded: the caller is already
    being answered ``MARKETPLACE_CHECKOUT_UNAVAILABLE`` (502), the row is already ``pending``
    with no provider reference - which is itself a truthful record of an attempt that did not
    complete - and re-raising would replace that 502 with a 503 that describes the bookkeeping
    rather than the checkout. Nothing downstream reads a different outcome because of it.
    """
    try:
        (
            supabase.table("library_subscriptions")
            .update(
                {
                    "status": _STATUS_PAYMENT_FAILED,
                    "failure_cause": _truncate(cause),
                    "failed_at": instant.isoformat(),
                }
            )
            .eq("id", subscription_id)
            .eq("status", _STATUS_PENDING)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - logged; see the docstring for why it stops here
        logger.error(
            "could not mark subscription %s payment_failed (it remains pending, not deleted): "
            "%s",
            subscription_id,
            exc,
        )


# ══════════════════════════════════════════════════════════════════════════
# THE PROVIDER CALL (Requirement 9.13 - the 30-second deadline)
# ══════════════════════════════════════════════════════════════════════════


async def _request_provider_session(
    factory: ProviderSessionFactory,
    context: CheckoutContext,
    deadline_seconds: float,
) -> ProviderSession:
    """Ask ``factory`` for a session, abandoning the attempt after ``deadline_seconds``.

    A synchronous factory - which both SDKs are - is dispatched to a worker thread so the
    deadline can actually be applied; awaiting a blocking call on the event loop would make
    ``asyncio.wait_for`` unable to fire and would stall every other request in the process.

    Note what abandoning does and does not do: ``wait_for`` stops *this* request waiting, and
    the thread the blocking SDK call is running on is not killed by it. That is the correct
    trade for Requirement 9.13, whose subject is the request - the alternative, holding the
    caller open until the provider's own socket timeout, is the condition the deadline exists to
    bound. The row is marked ``payment_failed`` either way, and a session the provider creates
    after the deadline carries a reference this row does not record, so it cannot activate a
    subscription.

    Raises:
        ProviderSessionFailed: the deadline passed (``timed_out=True``) or the factory raised.
    """
    try:
        if inspect.iscoroutinefunction(factory):
            session = await asyncio.wait_for(factory(context), timeout=deadline_seconds)
        else:
            session = await asyncio.wait_for(
                asyncio.to_thread(factory, context), timeout=deadline_seconds
            )
    except asyncio.TimeoutError as exc:
        logger.error(
            "provider %s did not answer within %ss for subscription %s",
            context.provider,
            deadline_seconds,
            context.subscription_id,
        )
        raise ProviderSessionFailed(
            f"{context.provider} session request exceeded the "
            f"{deadline_seconds} second deadline",
            timed_out=True,
        ) from exc
    except ProviderSessionFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - converted to the defined outcome, not swallowed
        logger.error(
            "provider %s session creation failed for subscription %s: %s",
            context.provider,
            context.subscription_id,
            exc,
        )
        raise ProviderSessionFailed(
            f"{context.provider} session creation failed: {type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(session, ProviderSession):
        raise ProviderSessionFailed(
            f"{context.provider} factory returned {type(session).__name__}, "
            "not a ProviderSession"
        )
    return session


def default_provider_session_factory(context: CheckoutContext) -> ProviderSession:
    """Construct the session at Stripe or Razorpay. The one place either SDK is imported.

    Blocking on purpose: both SDKs are synchronous, and :func:`_request_provider_session`
    dispatches this to a worker thread so the deadline applies. Credentials come from
    :func:`_provider_credentials`, which delegates to ``billing._validate_keys`` - so the
    ``"sk_test_dummy"`` default the old handler carried does not exist on this path.
    """
    if context.provider == "stripe":
        return _stripe_session(context)
    if context.provider == "razorpay":
        return _razorpay_order(context)
    raise ProviderSessionFailed(
        f"no provider session builder for {context.provider!r}; Requirement 9.1 permits "
        f"only {sorted(SUPPORTED_PROVIDERS)}"
    )


def _provider_credentials(provider: str) -> str:
    """The validated provider key, through ``billing._validate_keys``.

    Reused rather than reimplemented: ``_validate_keys`` is the single place that refuses an
    absent key, the ``sk_test_dummy`` / ``rzp_test_dummy`` placeholders and a non-live
    credential. It raises ``fastapi.HTTPException``, which is a *router* outcome, so it is
    translated into :class:`ProviderSessionFailed` here - the service's own outcome, which lands
    the row in ``payment_failed`` and answers the caller 502 like any other pre-session failure.

    Raises:
        ProviderSessionFailed: the credential is missing, a placeholder, or not a live key.
    """
    try:
        from backend_app.routers.billing import _validate_keys
    except Exception as exc:  # noqa: BLE001 - converted to the defined outcome, not swallowed
        raise ProviderSessionFailed(
            f"the billing credential validator is unavailable: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        return _validate_keys(provider)
    except ProviderSessionFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - HTTPException included; re-raised as our outcome
        detail = getattr(exc, "detail", None) or str(exc)
        raise ProviderSessionFailed(
            f"{provider} credentials are not usable on this path: {detail}"
        ) from exc


def _settlement_metadata(context: CheckoutContext) -> Dict[str, str]:
    """The facts a payment event has to carry back, built ONCE for both providers.

    Task 19.16. Every place a provider is told about this purchase reads this one object, so the
    Stripe session metadata, the Stripe PaymentIntent metadata and the Razorpay order notes cannot
    disagree about which Subscription a payment - or a refund of it - belongs to.

    ``item_key`` is what ``billing._apply_billing_entitlement`` dispatches on
    (``startswith("marketplace_")``) and ``subscription_id`` is what the settlement path needs.
    The Razorpay builder adds ``item`` as well, which is that provider's existing spelling of the
    same value; it adds no second value.
    """
    return {
        "user_id": context.purchaser_id,
        "subscription_id": context.subscription_id,
        "library_id": context.listing_id,
        "item_key": f"marketplace_{context.listing_id}",
        "currency": context.currency,
    }


def _stripe_session(context: CheckoutContext) -> ProviderSession:
    """A one-off Stripe Checkout Session for exactly ``context.amount_minor``.

    ``unit_amount`` is the Listing's ``price_minor`` **verbatim** - Stripe's unit is the minor
    unit, so there is nothing to convert and no ``float`` to lose precision in. A ``19.99``
    Listing is stored as ``1999`` and charged as ``1999``.

    ``mode="payment"`` rather than ``"subscription"``: the Subscription_Period is this system's
    own calendar-month arithmetic (``subscription_period.period_for_activation``), driven by the
    confirmed payment instant, and handing the recurrence to Stripe would make the provider's
    billing anniversary a second source of truth for when access ends.

    ``metadata`` reproduces the contract ``billing._apply_billing_entitlement`` already reads:
    it dispatches on ``item_key.startswith("marketplace_")`` and
    ``_apply_marketplace_entitlement`` reads ``metadata["subscription_id"]``. The existing
    webhook therefore activates this subscription; no second webhook handler exists.
    """
    api_key = _provider_credentials("stripe")
    try:
        import stripe
    except Exception as exc:  # noqa: BLE001 - converted to the defined outcome, not swallowed
        raise ProviderSessionFailed(
            f"the stripe SDK is not importable: {type(exc).__name__}: {exc}"
        ) from exc

    stripe.api_key = api_key
    frontend = _frontend_base_url()
    metadata = _settlement_metadata(context)
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[
            {
                "price_data": {
                    "currency": context.currency.lower(),
                    "product_data": {"name": f"Strategy subscription: {context.listing_name}"},
                    "unit_amount": context.amount_minor,
                },
                "quantity": 1,
            }
        ],
        mode="payment",
        success_url=(
            f"{frontend}/marketplace"
            f"?session_id={{CHECKOUT_SESSION_ID}}&sub_id={context.subscription_id}"
        ),
        cancel_url=f"{frontend}/marketplace",
        client_reference_id=context.purchaser_id,
        metadata=dict(metadata),
        # WHY THE SAME METADATA TWICE (task 19.16, Requirements 10.4, 10.8): Stripe copies session
        # metadata to the Checkout Session object only. The Charge and PaymentIntent - which are
        # what ``charge.refunded`` delivers - carry metadata only when the session asked for it
        # here. Without this, a refund event arrives with no ``item_key`` and no
        # ``subscription_id`` at all. The value is the SAME object as ``metadata`` above, so the
        # session and the payment cannot disagree about which Subscription this is.
        #
        # It is belt and braces, not the mechanism: ``billing._settle_marketplace_reversal``
        # correlates a refund by reading the payment's own Settlement_Record back from the ledger
        # (``settlement_service.find_settled_payment``), which does not depend on the provider
        # having copied anything.
        payment_intent_data={"metadata": dict(metadata)},
    )
    reference = _as_text(_get(session, "id"))
    if not reference:
        raise ProviderSessionFailed("stripe returned a session with no id")
    return ProviderSession(
        provider="stripe",
        reference=reference,
        checkout_url=_as_text(_get(session, "url")),
    )


def _razorpay_order(context: CheckoutContext) -> ProviderSession:
    """A Razorpay order for exactly ``context.amount_minor`` paise.

    ``amount`` is ``price_minor`` verbatim, for the same reason Stripe's ``unit_amount`` is:
    Razorpay's unit is the paisa. ``notes["item"]`` is the spelling the existing Razorpay
    webhook reads (Stripe's is ``metadata["item_key"]``), so both halves of the one
    Billing_Integration keep working unchanged.

    ``razorpay_key`` travels back on ``extra`` because the browser needs the key **id** to open
    the checkout; the secret is read here and never leaves this process. ``billing`` already
    returns the same id on its own Razorpay path.
    """
    key_id = _provider_credentials("razorpay")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
    if not key_secret:
        raise ProviderSessionFailed("the razorpay key secret is not configured")

    try:
        import razorpay
    except Exception as exc:  # noqa: BLE001 - converted to the defined outcome, not swallowed
        raise ProviderSessionFailed(
            f"the razorpay SDK is not importable: {type(exc).__name__}: {exc}"
        ) from exc

    client = razorpay.Client(auth=(key_id, key_secret))
    # The same object Stripe carries (task 19.16), plus ``item`` - this provider's own spelling of
    # ``item_key``, which the existing Razorpay webhook branch reads. Both spellings are written so
    # ``billing._razorpay_settlement_metadata`` finds one whichever it looks for, and neither can
    # drift from the Stripe side because both come from ``_settlement_metadata``.
    #
    # Razorpay propagates order notes to the payment entity, but a REFUND entity does not reliably
    # carry them, which is why refund correlation goes through the ledger rather than through
    # these notes.
    metadata = _settlement_metadata(context)
    order = client.order.create(
        {
            "amount": context.amount_minor,
            "currency": context.currency,
            "receipt": f"marketplace_sub_{context.subscription_id}",
            "notes": dict(metadata, item=metadata["item_key"]),
        }
    )
    reference = _as_text(_get(order, "id"))
    if not reference:
        raise ProviderSessionFailed("razorpay returned an order with no id")
    return ProviderSession(
        provider="razorpay",
        reference=reference,
        extra={"razorpay_key": key_id, "amount": context.amount_minor},
    )


def _frontend_base_url() -> str:
    """The frontend origin the provider redirects back to, without a trailing slash."""
    return (os.environ.get("FRONTEND_URL") or "http://localhost:5173").rstrip("/")


# ══════════════════════════════════════════════════════════════════════════
# REFUSALS
# ══════════════════════════════════════════════════════════════════════════


def _not_purchasable(listing_id: str, *, reason: str, **extra: Any) -> MarketplaceError:
    """``MARKETPLACE_LISTING_NOT_PURCHASABLE`` (409) with the caller's own values in ``details``.

    The public sentence comes from the catalogue and carries no digit, table name or internal
    threshold; ``details`` carries only ``reason`` and identifiers the caller supplied or already
    holds, so the body discloses nothing about another user or about the Listing's internals
    (Requirement 22.9).
    """
    details: Dict[str, Any] = {"listing_id": listing_id, "reason": reason}
    details.update({k: v for k, v in extra.items() if v is not None})
    return MarketplaceError(MARKETPLACE_LISTING_NOT_PURCHASABLE, details=details)


def _is_published(listing: Mapping[str, Any]) -> bool:
    """Whether the Listing's Submission is ``PUBLISHED`` (Requirement 9.10).

    ``PUBLIC_STATES`` is read rather than ``== "PUBLISHED"`` re-spelled, so this gate and the
    Listing_Projection's cannot drift. A Listing may carry more than one Submission row over its
    life (a resubmission after a rejection), so the Listing is purchasable when **any** embedded
    Submission is in a public state. A Listing with no Submission at all is not purchasable -
    nothing has been published, and inferring publication from ``moderation_status`` is the
    inference Requirement 4.12's single shared mapping exists to remove.
    """
    public = {state.value for state in PUBLIC_STATES}
    for submission in _as_row_list(_get(listing, "marketplace_submissions")):
        state = _get(submission, "submission_state")
        if state is not None and str(state).strip().upper() in public:
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════
# SMALL PURE UTILITIES
# ══════════════════════════════════════════════════════════════════════════


def _rows(response: Any) -> List[Mapping[str, Any]]:
    """The rows of a PostgREST response, or ``[]``. Raises when the response signals an error.

    Same reading as ``entitlement_resolver._rows``: rows may arrive on ``.data`` (the
    ``supabase-py`` convention this codebase uses), under a ``["data"]`` key, as a single
    mapping (what ``.single()`` returns - the shape the old handler indexed with ``[0]`` and got
    ``KeyError: 0`` for), or as a bare list in a test double.

    A response carrying a non-empty ``error`` is a read that DID NOT COMPLETE and raises
    :class:`CheckoutReadFailed`; reading it as "no rows" is what would turn a broken read into a
    404 or a second charge.
    """
    error = getattr(response, "error", None)
    if error is None and isinstance(response, Mapping):
        error = response.get("error")
    if error:
        raise CheckoutReadFailed(f"the read returned an error: {error}")

    if response is None:
        return []
    if isinstance(response, Mapping):
        data = response.get("data", response) if "data" in response else response
    else:
        data = getattr(response, "data", response)
    if data is None:
        return []
    if isinstance(data, Mapping):
        return [data]
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        return [r for r in data if isinstance(r, Mapping)]
    return []


def _as_row_list(value: Any) -> List[Mapping[str, Any]]:
    """A PostgREST embedded resource as a list of mappings. Handles a single mapping too."""
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [r for r in value if isinstance(r, Mapping)]
    return []


def _get(obj: Any, key: str) -> Any:
    """Read ``key`` from a mapping or as an attribute off an object. ``None`` when absent."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _as_text(value: Any) -> Optional[str]:
    """``value`` as text, or ``None`` when it is ``None``. Ids may be UUIDs or ints."""
    if value is None:
        return None
    return str(value)


def _require_text(value: Any, what: str) -> str:
    """``value`` as non-empty text, or ``ValueError``.

    A blank caller id or listing id is a programming error at the call site, not a refusal the
    caller can act on: the route resolves both through ``_safe_uuid`` before calling here.
    """
    text = _as_text(value)
    if not text or not text.strip():
        raise ValueError(f"{what} is required")
    return text.strip()


def _same_id(a: Any, b: Any) -> bool:
    """Whether two identifiers denote the same row. ``None`` never equals anything.

    So an absent ``author_id`` cannot make a caller the owner of a Listing by accident - which
    would turn a purchase into a 400 ``MARKETPLACE_OWN_LISTING`` for a Listing nobody owns.
    """
    if a is None or b is None:
        return False
    return str(a) == str(b)


def _normalise_currency(value: Any) -> str:
    """A currency code as upper-case text. ``''`` when absent, which matches no Listing."""
    if value is None:
        return ""
    return str(value).strip().upper()


def _normalise_status(value: Any) -> Optional[str]:
    """A ``library_subscriptions.status`` value as lowercase text, or ``None``."""
    if value is None:
        return None
    return str(value).strip().lower()


def _utc_now() -> datetime:
    """The current instant in UTC. The only clock read in this module."""
    return datetime.now(timezone.utc)


def _coerce_instant(value: Optional[datetime]) -> datetime:
    """``value`` as a tz-aware UTC instant, defaulting to now.

    A naive datetime is read as UTC rather than rejected: the only callers are this module's own
    default and a test pinning the timestamp, and neither has a local zone to preserve.
    """
    if value is None:
        return _utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _truncate(text: str) -> str:
    """``text`` bounded to :data:`_MAX_FAILURE_CAUSE_CHARS`, for the ``failure_cause`` column."""
    flattened = " ".join(str(text).split())
    if len(flattened) <= _MAX_FAILURE_CAUSE_CHARS:
        return flattened
    return flattened[: _MAX_FAILURE_CAUSE_CHARS - 1] + "…"
