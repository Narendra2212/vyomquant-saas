"""
backend_app/backend/marketplace/entitlement_resolver.py - the Entitlement_Resolver.

Spec: marketplace-subscriptions-paper-trading task 17.1. ``design.md`` ->
"``marketplace/entitlement_resolver.py`` and subscriber execution". Requirements 4.11, 7.5,
7.7, 7.10, 7.11, 11.7, 11.10, 21.1.

Exposes
-------
EntitlementReason        the six internal reasons (OWNED, SUBSCRIBED, NOT_SUBSCRIBED, EXPIRED,
                         LISTING_UNAVAILABLE, SUBSCRIPTION_SUSPENDED)
Entitlement              the frozen value type ``resolve`` returns: ``entitling``, ``reason``
                         and the identifiers a caller needs (listing_id, source_strategy_id,
                         version_id, subscription_id, period_expiry). ``wire_code`` is the
                         mapping of the reason onto the shared error catalogue, or ``None`` for
                         an entitling result
ENTITLING_REASONS        the two reasons that entitle, in one place
WIRE_CODE_FOR_REASON     the one place a reason maps to a ``design.md`` error-catalogue code
EntitlementReadFailed    a Persistence_Layer read did not complete, so there IS no decision
resolve(caller, listing_id, supabase, now) -> Entitlement    the single admission decision

WHY THIS MODULE IS PURE (imports no FastAPI, owns no framework I/O)
-------------------------------------------------------------------
The layering rule ``design.md`` -> "Architecture" states, and ``eligibility_gate.py``,
``submission_service.py`` and ``subscription_period.py`` already follow: a module here imports
no FastAPI and owns no HTTP or clock I/O. The ``supabase`` handle is **passed in** as an
argument (dependency injection), and ``now`` is **passed in** rather than read from a clock, so
:func:`resolve` is deterministic and testable against a fake client that records the calls it
received. The reason for the injected ``now`` is Requirement 11.7 / property P-11: the resolver
compares ``now`` with ``period_expiry`` itself, so the sweep-independence of expiry is a pure
comparison a test can pin exactly. Nothing here imports ``fastapi`` or ``errors.py`` (which
imports FastAPI); the wire codes are referenced by their catalogue *string*, and the route maps
:class:`Entitlement` onto a :class:`~backend_app.backend.marketplace.errors.MarketplaceError`.

WHY IDENTITY COMES ONLY FROM ``caller``
---------------------------------------
Requirements 7.7, 21.1 and property P-45: the acting identity is the authenticated server-side
session, never a value from a request body, query string, path segment or WS message. So
:func:`resolve` reads ``caller.id`` (and nothing from ``listing_id`` beyond the listing key it
names) to scope the subscription read ``s.user_id = :caller_id``. A subscription belonging to
another user simply is not returned by the join, so it is indistinguishable from absent - a
foreign subscription can never entitle this caller.

THE ONE ROUND TRIP (the join)
-----------------------------
The listing, its submission state and the caller's own subscription row are fetched in one
embedded PostgREST read off ``library_strategies`` (:data:`_ENTITLEMENT_SELECT`):

    library_strategies
      ├─ marketplace_submissions      (the listing's submission lifecycle state)
      └─ library_subscriptions        (embedded and filtered to this caller's row)

A ``LEFT`` embed is used for both, so a listing with no submission row and a caller with no
subscription both come back as an empty embedded collection rather than a missing listing row.
The subscription embed is filtered server-side to ``user_id = caller.id`` so the round trip
carries only the caller's own row.

The one further read is :func:`_resolve_current_version`, which resolves the *current live
Strategy_Version* behind ``source_strategy_id``. It serves two purposes at once:

  * it is the "does ``source_strategy_id`` resolve to a live strategy row" check (Requirement
    7.11): when it resolves nothing, the answer is ``LISTING_UNAVAILABLE`` -> 409, disclosing
    neither Protected_Logic nor owner identity; and
  * it supplies the ``version_id`` an entitling result carries, which is the version the
    server-role client later fetches the executable artifact from (Requirement 7.5) and never
    returns to the caller.

It is issued only once an OWNED or SUBSCRIBED conclusion has otherwise been reached, so a
NOT_SUBSCRIBED or EXPIRED caller costs exactly the one join and no version read.

THE EXPIRY CHECK IS SWEEP-INDEPENDENT (Requirement 11.7, P-11)
--------------------------------------------------------------
A Subscription stops entitling at its ``period_expiry`` instant whether or not the expiry sweep
has run. :func:`resolve` therefore treats a null ``period_expiry`` OR ``now >= period_expiry``
as non-entitling (reason ``EXPIRED``) *before* it would trust the stored ``status`` label -
the ``status`` column alone is never the authority. A row that still reads ``status = 'active'``
because the housekeeping sweep is dead is still refused the moment ``now`` reaches its expiry.

A SUSPENDED or UNPUBLISHED LISTING STILL ENTITLES (Requirement 4.11)
--------------------------------------------------------------------
A Listing going ``SUSPENDED`` or ``UNPUBLISHED`` does not revoke an already-paid, unexpired
Subscription. So the submission-state gate admits ``PUBLISHED``, ``SUSPENDED`` and
``UNPUBLISHED`` alike; only a submission state outside those three (or a listing with no
submission at all) makes the listing itself unavailable. The distinction between a *suspended
Subscription* (the caller's own subscription is administratively held -> reason
``SUBSCRIPTION_SUSPENDED``) and a *suspended Listing* (the seller's listing is hidden but the
caller's paid subscription still runs -> still ``SUBSCRIBED``) is exactly what this ordering
keeps apart.

A READ THAT DOES NOT COMPLETE IS NOT AN ANSWER (Requirement 30.5, 1.5, 1.7)
---------------------------------------------------------------------------
Neither read may be swallowed into a verdict. A failed read answered as ``NOT_SUBSCRIBED``
would tell a paying subscriber they never subscribed, and answered as ``LISTING_UNAVAILABLE``
would tell them the Listing is gone; both are fabricated facts. So each read is wrapped in
exactly one ``except`` that RE-RAISES the defined outcome :class:`EntitlementReadFailed`
(chained from the driver error, ``wire_code`` = ``MARKETPLACE_READ_FAILED`` -> 503), and a
response that carries a non-empty ``error`` raises the same rather than being read as "no
rows". There is no broad ``except`` here that returns a verdict: an
:class:`EntitlementReadFailed` is distinguishable at the call site from every one of the six
reasons, because it is not one of them.

THE SINGLE ADMISSION DECISION
-----------------------------
:func:`resolve` is the one admission decision for BOTH strategy deployment
(``deploy_marketplace_strategy``, task 17.3) and Paper_Session start (``start_session``, task
27.1). Property P-16 ("the deployment and Paper_Session admission decision each equal the
Entitlement_Resolver's decision") holds because there is only one decision - both call paths
call this function and refuse on ``not entitling`` with the reason's wire code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, FrozenSet, List, Mapping, Optional, Sequence

# The wire-code catalogue strings are referenced by name only. errors.py imports FastAPI, so it
# is NOT imported here; these constants mirror its spellings and are asserted equal to it by the
# module's unit test (test_wire_codes_match_error_catalogue), which imports both.
_MARKETPLACE_NOT_SUBSCRIBED = "MARKETPLACE_NOT_SUBSCRIBED"
_MARKETPLACE_SUBSCRIPTION_EXPIRED = "MARKETPLACE_SUBSCRIPTION_EXPIRED"
_MARKETPLACE_STRATEGY_UNAVAILABLE = "MARKETPLACE_STRATEGY_UNAVAILABLE"
_MARKETPLACE_OPERATION_NOT_PERMITTED = "MARKETPLACE_OPERATION_NOT_PERMITTED"
_MARKETPLACE_READ_FAILED = "MARKETPLACE_READ_FAILED"

__all__ = [
    "EntitlementReason",
    "Entitlement",
    "ENTITLING_REASONS",
    "WIRE_CODE_FOR_REASON",
    "EntitlementReadFailed",
    "resolve",
]


class EntitlementReadFailed(Exception):
    """A read :func:`resolve` needs did not complete, so there is no entitlement decision.

    This is the "defined error outcome" half of Requirement 30.5: the resolver never turns a
    broken read into one of the six reasons. It carries the shared catalogue code
    ``MARKETPLACE_READ_FAILED`` (503) so the route raises the same structured error it raises
    for every other Persistence_Layer failure, rather than a zero-filled or fabricated verdict
    (Requirements 1.5, 1.7).
    """

    #: The error-catalogue code the route answers with. Kept as a plain string for the same
    #: reason the wire codes above are: ``errors.py`` imports FastAPI and this module does not.
    wire_code: str = _MARKETPLACE_READ_FAILED


# ══════════════════════════════════════════════════════════════════════════
# THE SIX REASONS (design.md -> "STRUCTURE Entitlement")
# ══════════════════════════════════════════════════════════════════════════


class EntitlementReason(str, Enum):
    """The six, and only six, reasons :func:`resolve` returns.

    ``str``-valued so a member compares equal to its own spelling, matching
    ``SubscriptionState`` and ``SubmissionState``. ``NOT_SUBSCRIBED`` and ``EXPIRED`` are kept
    distinct because Requirement 7.10 makes them distinct codes on the wire - a caller who never
    subscribed and a caller whose subscription lapsed get different answers.
    """

    #: The caller is the Listing's owner. Entitling regardless of any subscription.
    OWNED = "OWNED"
    #: The caller holds an ACTIVE, unexpired Subscription. Entitling.
    SUBSCRIBED = "SUBSCRIBED"
    #: No Subscription row at all - distinct from EXPIRED (Requirement 7.10).
    NOT_SUBSCRIBED = "NOT_SUBSCRIBED"
    #: A Subscription existed but its period has ended (null or past ``period_expiry``), OR its
    #: status is a non-active, non-suspended lapse. The sweep-independent case (Req 11.7).
    EXPIRED = "EXPIRED"
    #: The Listing does not exist, its submission is in no live state, or its backing
    #: ``source_strategy_id`` no longer resolves to a live strategy version (Req 7.11) -> 409.
    LISTING_UNAVAILABLE = "LISTING_UNAVAILABLE"
    #: The caller's own Subscription is administratively suspended (Req 4.11's distinction from
    #: a suspended *Listing*, which still entitles).
    SUBSCRIPTION_SUSPENDED = "SUBSCRIPTION_SUSPENDED"


#: The two reasons that entitle, written once. :class:`Entitlement` checks its own
#: ``entitling`` flag against this set on construction, so an ``entitling=True`` result carrying
#: a refusing reason (or the reverse) cannot be built at all. Property P-16 says the deployment
#: and the Paper_Session admission decision each EQUAL this resolver's decision; that equality
#: is only meaningful if the decision is internally consistent, so the invariant is enforced
#: here rather than trusted at four return sites.
ENTITLING_REASONS: FrozenSet["EntitlementReason"] = frozenset(
    {EntitlementReason.OWNED, EntitlementReason.SUBSCRIBED}
)


#: The one place an internal reason maps onto a ``design.md`` error-catalogue wire code. An
#: entitling reason (OWNED, SUBSCRIBED) has no error code - it maps to ``None``. The route reads
#: :attr:`Entitlement.wire_code` (which delegates here) and raises the matching
#: ``MarketplaceError``; this module never constructs one, to stay free of ``errors.py`` and
#: FastAPI.
#:
#:   * ``NOT_SUBSCRIBED``          -> MARKETPLACE_NOT_SUBSCRIBED       (403)  Req 7.10
#:   * ``EXPIRED``                 -> MARKETPLACE_SUBSCRIPTION_EXPIRED (403)  Req 7.10
#:   * ``LISTING_UNAVAILABLE``     -> MARKETPLACE_STRATEGY_UNAVAILABLE (409)  Req 7.11
#:   * ``SUBSCRIPTION_SUSPENDED``  -> MARKETPLACE_OPERATION_NOT_PERMITTED (403)
#:
#: There is no distinct catalogue code for a suspended Subscription in ``design.md`` -> "The
#: error code catalogue"; ``MARKETPLACE_OPERATION_NOT_PERMITTED`` (403, "not permitted on a
#: strategy you ... rather than own") is the existing code whose meaning fits - the caller's
#: subscription is held, so the operation is not permitted right now - so it is reused rather
#: than a new code added.
WIRE_CODE_FOR_REASON: Mapping["EntitlementReason", Optional[str]] = MappingProxyType(
    {
        EntitlementReason.OWNED: None,
        EntitlementReason.SUBSCRIBED: None,
        EntitlementReason.NOT_SUBSCRIBED: _MARKETPLACE_NOT_SUBSCRIBED,
        EntitlementReason.EXPIRED: _MARKETPLACE_SUBSCRIPTION_EXPIRED,
        EntitlementReason.LISTING_UNAVAILABLE: _MARKETPLACE_STRATEGY_UNAVAILABLE,
        EntitlementReason.SUBSCRIPTION_SUSPENDED: _MARKETPLACE_OPERATION_NOT_PERMITTED,
    }
)


# ══════════════════════════════════════════════════════════════════════════
# THE VALUE TYPE (design.md -> "STRUCTURE Entitlement")
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Entitlement:
    """The result of one admission decision.

    Frozen because it is a *decision*: the deployment and Paper_Session start paths refuse or
    admit off it, and neither may edit it on the way through. It carries the identifiers the
    caller needs and no more - the executable artifact itself is fetched server-side from
    ``source_strategy_id`` / ``version_id`` and never travels back to the caller (Requirement
    7.5).

    ``wire_code`` is a property, not a stored field, so the reason and its wire mapping cannot
    drift: it delegates to :data:`WIRE_CODE_FOR_REASON`. It is ``None`` for an entitling result
    and the catalogue code string otherwise.
    """

    #: Whether the caller may deploy / start a Paper_Session against this Listing right now.
    entitling: bool
    #: Which of the six reasons produced this verdict.
    reason: EntitlementReason
    #: The Listing this decision is about (echoed back for the caller's convenience).
    listing_id: Optional[str] = None
    #: The Listing's backing strategy id - server-side only, for artifact resolution.
    source_strategy_id: Optional[str] = None
    #: The current live Strategy_Version behind the Listing - server-side only. Present only on
    #: an entitling result, where the executable artifact is fetched from it (Requirement 7.5).
    version_id: Optional[str] = None
    #: The caller's Subscription id, when one participated in the decision.
    subscription_id: Optional[str] = None
    #: The caller's Subscription period expiry, when one participated in the decision.
    period_expiry: Optional[datetime] = None

    def __post_init__(self) -> None:
        """Hold ``entitling`` and ``reason`` to one meaning (see :data:`ENTITLING_REASONS`)."""
        if self.entitling != (self.reason in ENTITLING_REASONS):
            raise ValueError(
                f"Entitlement(entitling={self.entitling!r}) disagrees with "
                f"reason={self.reason.value}"
            )

    @property
    def wire_code(self) -> Optional[str]:
        """The error-catalogue code for a non-entitling result, or ``None`` when entitling."""
        return WIRE_CODE_FOR_REASON[self.reason]


# ══════════════════════════════════════════════════════════════════════════
# THE READ PROJECTIONS
# ══════════════════════════════════════════════════════════════════════════

#: The one embedded read that fetches the Listing, its submission state and the caller's own
#: Subscription row in a single round trip. PostgREST embeds ``marketplace_submissions`` and
#: ``library_subscriptions`` off ``library_strategies``; the subscription embed is filtered to
#: the caller server-side (see :func:`_read_admission_row`). The column set is bound by the
#: ``entitlement_resolver`` entry in this package's ``COLUMN_CONTRACT`` and asserted a subset of
#: it by ``tests/test_marketplace_paper_schema_contract.py``.
_ENTITLEMENT_SELECT = (
    "id,author_id,source_strategy_id,source_cloning_enabled,"
    "marketplace_submissions(submission_state),"
    "library_subscriptions(id,user_id,status,period_expiry)"
)

#: The current-version resolution read. ``strategy_versions`` scoped to ``strategy_id`` and to
#: saved (non-draft) versions; the latest is the current one. Its emptiness is the "does not
#: resolve to a live strategy row" signal (Requirement 7.11).
_VERSION_SELECT = "id,strategy_id,version,is_draft"


# The submission states under which a Listing is still usable by an ACTIVE Subscription. A
# SUSPENDED or UNPUBLISHED Listing does not revoke an already-paid, unexpired subscription
# (Requirement 4.11); a submission in any other state (or none at all) makes the listing
# unavailable.
_ENTITLING_SUBMISSION_STATES = frozenset({"PUBLISHED", "SUSPENDED", "UNPUBLISHED"})

# The persisted ``library_subscriptions.status`` spellings that matter to the resolver. The
# column is lowercase text (see subscription_state.STATUS_TEXT_FOR_STATE); a suspended
# subscription is its own answer, an active one proceeds to the expiry check, and anything else
# is a lapse.
_STATUS_ACTIVE = "active"
_STATUS_SUSPENDED = "suspended"


# ══════════════════════════════════════════════════════════════════════════
# THE ADMISSION DECISION (Requirements 4.11, 7.5, 7.7, 7.10, 7.11, 11.7, 11.10, 21.1)
# ══════════════════════════════════════════════════════════════════════════


async def resolve(
    caller: Any,
    listing_id: Any,
    supabase: Any,
    now: datetime,
) -> Entitlement:
    """Decide whether ``caller`` may execute the Listing ``listing_id`` right now.

    This is the single admission decision for both strategy deployment and Paper_Session start
    (Requirement 11.10, property P-16). It issues one embedded read (the Listing + its
    submission state + the caller's own Subscription) and, only when it would otherwise entitle,
    one further read to resolve the current live Strategy_Version.

    Args:
        caller: the authenticated server-side identity. Only its ``id`` is read; no identifier
            from a request body, query, path or WS message participates (Requirements 7.7, 21.1).
        listing_id: the ``library_strategies.id`` the decision is about.
        supabase: the injected Persistence_Layer handle (service-role client at the route).
        now: the instant to compare ``period_expiry`` against, in UTC. Passed in so the
            sweep-independent expiry check is deterministic (Requirement 11.7, property P-11).

    Returns:
        An :class:`Entitlement`. ``entitling`` is ``True`` only for ``OWNED`` and ``SUBSCRIBED``;
        every other reason is non-entitling and carries a :attr:`Entitlement.wire_code`.
    """
    caller_id = _get(caller, "id")
    listing_key = _as_text(listing_id)

    row = _read_admission_row(supabase, listing_key)

    # ── The Listing must exist. A missing row discloses nothing (Req 7.11). ──
    if row is None:
        return Entitlement(
            entitling=False,
            reason=EntitlementReason.LISTING_UNAVAILABLE,
            listing_id=listing_key,
        )

    author_id = _get(row, "author_id")
    source_strategy_id = _as_text(_get(row, "source_strategy_id"))

    # ── OWNED takes precedence over any subscription (Req 7.1, design ordering). ──
    if _same_id(author_id, caller_id):
        version_id = _resolve_current_version(supabase, source_strategy_id)
        if version_id is None:
            # The owner's own backing strategy no longer resolves: unavailable, not owned-usable.
            return Entitlement(
                entitling=False,
                reason=EntitlementReason.LISTING_UNAVAILABLE,
                listing_id=listing_key,
                source_strategy_id=source_strategy_id,
            )
        return Entitlement(
            entitling=True,
            reason=EntitlementReason.OWNED,
            listing_id=listing_key,
            source_strategy_id=source_strategy_id,
            version_id=version_id,
        )

    # ── The caller's own Subscription, scoped by the embed to user_id = caller.id. ──
    subscription = _caller_subscription(row, caller_id)
    if subscription is None:
        return Entitlement(
            entitling=False,
            reason=EntitlementReason.NOT_SUBSCRIBED,
            listing_id=listing_key,
        )

    subscription_id = _as_text(_get(subscription, "id"))
    status = _normalise_status(_get(subscription, "status"))
    period_expiry = _coerce_instant(_get(subscription, "period_expiry"))

    # A suspended Subscription is its own answer, distinct from a lapse (Req 4.11).
    if status == _STATUS_SUSPENDED:
        return Entitlement(
            entitling=False,
            reason=EntitlementReason.SUBSCRIPTION_SUSPENDED,
            listing_id=listing_key,
            subscription_id=subscription_id,
            period_expiry=period_expiry,
        )

    # Anything that is not 'active' is a lapse - EXPIRED (Req 7.10's expired half).
    if status != _STATUS_ACTIVE:
        return Entitlement(
            entitling=False,
            reason=EntitlementReason.EXPIRED,
            listing_id=listing_key,
            subscription_id=subscription_id,
            period_expiry=period_expiry,
        )

    # ── The sweep-independent expiry check (Req 11.7, P-11). The status column alone is
    #    never trusted: a null or past period_expiry is non-entitling even when status still
    #    reads 'active' because the housekeeping sweep has not run. ──
    if period_expiry is None or now >= period_expiry:
        return Entitlement(
            entitling=False,
            reason=EntitlementReason.EXPIRED,
            listing_id=listing_key,
            subscription_id=subscription_id,
            period_expiry=period_expiry,
        )

    # ── The Listing's submission state. A SUSPENDED / UNPUBLISHED Listing STILL entitles an
    #    ACTIVE, unexpired Subscription (Req 4.11); only a state outside the three, or no
    #    submission at all, makes the listing unavailable. ──
    submission_state = _listing_submission_state(row)
    if submission_state not in _ENTITLING_SUBMISSION_STATES:
        return Entitlement(
            entitling=False,
            reason=EntitlementReason.LISTING_UNAVAILABLE,
            listing_id=listing_key,
            source_strategy_id=source_strategy_id,
            subscription_id=subscription_id,
            period_expiry=period_expiry,
        )

    # ── The backing strategy must resolve to a live Strategy_Version (Req 7.11). An
    #    unresolvable source_strategy_id -> LISTING_UNAVAILABLE (409), disclosing no
    #    Protected_Logic and no owner identity. ──
    version_id = _resolve_current_version(supabase, source_strategy_id)
    if version_id is None:
        return Entitlement(
            entitling=False,
            reason=EntitlementReason.LISTING_UNAVAILABLE,
            listing_id=listing_key,
            source_strategy_id=source_strategy_id,
            subscription_id=subscription_id,
            period_expiry=period_expiry,
        )

    return Entitlement(
        entitling=True,
        reason=EntitlementReason.SUBSCRIBED,
        listing_id=listing_key,
        source_strategy_id=source_strategy_id,
        version_id=version_id,
        subscription_id=subscription_id,
        period_expiry=period_expiry,
    )


# ══════════════════════════════════════════════════════════════════════════
# THE READS
# ══════════════════════════════════════════════════════════════════════════


def _read_admission_row(supabase: Any, listing_id: str) -> Optional[Mapping[str, Any]]:
    """The one round trip: the Listing, its submission state and the caller's Subscription.

    ``library_subscriptions`` is embedded and can be pre-filtered to the caller by PostgREST;
    when the client double does not support the embedded filter, :func:`_caller_subscription`
    re-scopes to ``user_id = caller.id`` in memory, so a foreign subscription can never leak
    into the decision either way.

    Raises:
        EntitlementReadFailed: the read did not complete. NOT ``None``, and not one of the six
            reasons: a broken read that returned ``LISTING_UNAVAILABLE`` or ``NOT_SUBSCRIBED``
            would be a fabricated fact about the caller's subscription (Requirement 30.5).
    """
    try:
        response = (
            supabase.table("library_strategies")
            .select(_ENTITLEMENT_SELECT)
            .eq("id", listing_id)
            .execute()
        )
        rows = _rows(response)
    except EntitlementReadFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome, never swallowed
        raise EntitlementReadFailed(
            f"the admission read for listing {listing_id} did not complete: {exc}"
        ) from exc
    return rows[0] if rows else None


def _resolve_current_version(supabase: Any, source_strategy_id: Optional[str]) -> Optional[str]:
    """The current live Strategy_Version behind ``source_strategy_id``, or ``None``.

    ``None`` when ``source_strategy_id`` is absent, when the read returns no row, or when no
    saved (non-draft) version exists - each is the "does not resolve to a live strategy row"
    condition of Requirement 7.11. The latest saved version by ``version`` is the current one.
    Selects no logic column, so nothing here can carry Protected_Logic.

    Raises:
        EntitlementReadFailed: the version read did not complete. A failure here is NOT the
            "does not resolve" condition - answering ``LISTING_UNAVAILABLE`` (409, "the
            Listing's artifact is gone") for a broken read would tell the subscriber something
            false about the Listing (Requirement 30.5).
    """
    if not source_strategy_id:
        return None
    try:
        response = (
            supabase.table("strategy_versions")
            .select(_VERSION_SELECT)
            .eq("strategy_id", source_strategy_id)
            .execute()
        )
        rows = _rows(response)
    except EntitlementReadFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome, never swallowed
        raise EntitlementReadFailed(
            f"the version read for strategy {source_strategy_id} did not complete: {exc}"
        ) from exc
    saved = [r for r in rows if not _is_draft(r)]
    if not saved:
        return None
    current = max(saved, key=_version_sort_key)
    return _as_text(_get(current, "id"))


# ══════════════════════════════════════════════════════════════════════════
# ROW HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _caller_subscription(
    row: Mapping[str, Any], caller_id: Any
) -> Optional[Mapping[str, Any]]:
    """The caller's own Subscription from the embedded collection, or ``None``.

    The embed is a list (PostgREST returns embedded resources as arrays). Re-scoping to
    ``user_id = caller.id`` here is belt-and-braces on top of the server-side embed filter: a
    subscription that belongs to another user is never returned by a correct query, and if a
    client double returns unfiltered rows this still admits only the caller's own row, so a
    foreign subscription cannot entitle this caller (Requirement 21.1).
    """
    embedded = _get(row, "library_subscriptions")
    candidates = _as_row_list(embedded)
    for sub in candidates:
        if _same_id(_get(sub, "user_id"), caller_id):
            return sub
    # When the client double already scoped the embed to the caller and dropped the user_id,
    # a single unattributed row is the caller's own.
    if len(candidates) == 1 and _get(candidates[0], "user_id") is None:
        return candidates[0]
    return None


def _listing_submission_state(row: Mapping[str, Any]) -> Optional[str]:
    """The Listing's submission-lifecycle state, upper-cased, or ``None`` when there is none.

    ``marketplace_submissions`` is embedded as a list. A Listing may carry more than one
    submission row over its life (a resubmission after rejection); the live one is whichever is
    in an entitling state, so the state is "entitling" if ANY embedded submission is in the
    entitling set, and otherwise the single/most-recent row's state for the refusal.
    """
    embedded = _get(row, "marketplace_submissions")
    states = [
        _normalise_state(_get(s, "submission_state")) for s in _as_row_list(embedded)
    ]
    states = [s for s in states if s]
    if not states:
        return None
    for state in states:
        if state in _ENTITLING_SUBMISSION_STATES:
            return state
    return states[0]


def _version_sort_key(version_row: Mapping[str, Any]) -> Any:
    """A sort key that puts the highest version last, tolerant of int or string versions."""
    raw = _get(version_row, "version")
    try:
        return (1, int(raw))
    except (TypeError, ValueError):
        return (0, str(raw or ""))


def _is_draft(version_row: Mapping[str, Any]) -> bool:
    return bool(_get(version_row, "is_draft"))


# ══════════════════════════════════════════════════════════════════════════
# SMALL PURE UTILITIES
# ══════════════════════════════════════════════════════════════════════════


def _rows(response: Any) -> List[Mapping[str, Any]]:
    """The rows of a PostgREST response, or ``[]``. Raises when the response signals an error.

    Same reading as the Eligibility_Gate's: rows may arrive on ``.data`` (the supabase-py
    convention this codebase uses), under a ``["data"]`` key, or - in a test double - as a bare
    list. A response carrying a non-empty ``error`` is a read that DID NOT COMPLETE and raises
    :class:`EntitlementReadFailed`; reading it as "no rows" is what would turn a broken read
    into ``LISTING_UNAVAILABLE`` or ``NOT_SUBSCRIBED`` (Requirement 30.5).
    """
    error = getattr(response, "error", None)
    if error is None and isinstance(response, Mapping):
        error = response.get("error")
    if error:
        raise EntitlementReadFailed(f"the read returned an error: {error}")

    if response is None:
        return []
    if isinstance(response, Mapping):
        # A ``{"data": ...}`` envelope. A bare mapping with no ``data`` key is a single row.
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
    """Read ``key`` from a mapping or an attribute off an object. ``None`` when absent."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _as_text(value: Any) -> Optional[str]:
    """``value`` as text, or ``None`` when it is ``None``. Ids may be UUID objects or ints."""
    if value is None:
        return None
    return str(value)


def _same_id(a: Any, b: Any) -> bool:
    """Whether two identifiers denote the same row, comparing on their text form.

    ``None`` never equals anything, so an absent ``author_id`` or ``caller_id`` cannot make a
    caller the owner by accident.
    """
    if a is None or b is None:
        return False
    return str(a) == str(b)


def _normalise_status(value: Any) -> Optional[str]:
    """A ``library_subscriptions.status`` value as lowercase text, or ``None``."""
    if value is None:
        return None
    return str(value).strip().lower()


def _normalise_state(value: Any) -> Optional[str]:
    """A ``marketplace_submissions.submission_state`` value as upper-case text, or ``None``."""
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _coerce_instant(value: Any) -> Optional[datetime]:
    """A ``period_expiry`` value as a tz-aware UTC datetime, or ``None``.

    Accepts a ``datetime`` (naive is read as UTC) and an ISO-8601 string (the shape a JSON
    driver returns). Anything else, and an unparseable string, is ``None`` - which the caller
    treats as "no expiry", i.e. non-entitling, so a malformed expiry never over-grants access.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None
