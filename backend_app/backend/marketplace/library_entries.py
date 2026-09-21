"""
backend_app/backend/marketplace/library_entries.py - the Strategies_Page combined-list builder.

Spec: marketplace-subscriptions-paper-trading task 17.4. ``design.md`` ->
"``algo22-terminal/src/pages/Strategies.jsx``" (the ``ownership`` / ``allowed_actions`` table)
and ``design.md`` -> "Fixed round trips" (the Strategies_Page's three).
Requirements 12.1, 12.2, 12.3, 12.4, 12.6, 27.2.

Exposes
-------
OWNERSHIP_OWNED / OWNERSHIP_SUBSCRIBED   the two, and only two, ``ownership`` labels (Req 12.2)
SUBSCRIBER_PERMITTED_ACTIONS             the eight actions a ``SUBSCRIBED`` entry may offer
SUBSCRIBER_FORBIDDEN_ACTIONS             the thirteen it may never offer (Requirement 12.4)
OWNER_ACTIONS                            every existing action, for an ``OWNED`` entry
RENEWAL_ENABLED / RENEWAL_CANCELLED      the two renewal states (Requirement 12.6)
OWNED_STRATEGY_SELECT                    round trip 1's explicit ``strategies`` column list
SUBSCRIPTION_SELECT                      round trip 2's ``library_subscriptions`` projection,
                                         with the embedded ``library_strategies!inner(...)``
RUNNING_PAPER_SESSION_SELECT             round trip 3's ``paper_sessions`` column list
RUNNING_SESSION_STATE                    the ``session_state`` value that counts as running
allowed_actions_for_owned()              -> the owner's action list
allowed_actions_for_subscribed(...)      -> the subscriber's action list (Reqs 12.3, 12.4, 12.5)
subscription_view(row, now)              -> ``{state, period_expiry, renewal_state}`` (Req 12.6)
entitlement_reason(subscription, listing, now)   -> the resolver's reason, without its reads
project_subscribed_listing(row)          -> the public, allow-listed view of a subscribed Listing
build_my_strategies_entries(...)         -> the whole combined list from the three read results

WHY THIS MODULE IS PURE (imports no FastAPI, owns no I/O, reads no clock)
------------------------------------------------------------------------
The layering rule ``design.md`` -> "Architecture" states, and ``money.py``,
``listing_projection.py``, ``subscription_period.py`` and ``entitlement_resolver.py`` already
follow. Every input arrives as an argument - the three read results and ``now`` - so the entire
derivation of ``ownership``, ``subscription`` and ``allowed_actions`` is decidable with no
database, no fixture and no event loop. That is what makes Requirement 12.4's "never these
thirteen actions" a property a test can assert exhaustively rather than sample.

WHY ``allowed_actions`` IS SERVER-COMPUTED AND WHY IT IS AN ALLOW-LIST
---------------------------------------------------------------------
Requirement 12.2 forbids a client-side inference of the ``OWNED``/``SUBSCRIBED`` label, and
Requirement 12.4 forbids a ``SUBSCRIBED`` entry ever *offering* one of thirteen named actions.
A deny-list is default-open: an action added to the page tomorrow is offered on a subscribed
entry the moment it exists. So :data:`SUBSCRIBER_PERMITTED_ACTIONS` is the *upper bound* and
:func:`allowed_actions_for_subscribed` builds its answer by intersecting with it, then raises
if the result ever meets :data:`SUBSCRIBER_FORBIDDEN_ACTIONS`. The raise is a real runtime
check, not a comment, and it is an explicit ``raise`` rather than ``assert`` so ``python -O``
cannot strip it.

``allowed_actions`` is an affordance list, not an authorisation. Requirement 12.7 keeps the
server the authority: each restricted route answers 403 on its own, and this list exists so the
page does not offer an action that refusal is certain for.

WHY THE ENTITLEMENT DECISION IS MIRRORED, NOT RE-RESOLVED PER ENTRY
------------------------------------------------------------------
``entitlement_resolver.resolve`` is the single admission decision, and it is *per listing*: one
embedded read plus, when it would otherwise entitle, one version read. Calling it once per
entry would make the round-trip count grow with the entry count, which is exactly what
Requirement 27.2 forbids. So :func:`entitlement_reason` re-states the resolver's decision
*ordering* over rows the batch read already carries, in the resolver's own vocabulary
(:class:`~backend_app.backend.marketplace.entitlement_resolver.EntitlementReason`) and with its
own wire-code mapping, so the two cannot drift in meaning:

  1. no Listing row                                     -> ``LISTING_UNAVAILABLE``
  2. ``status = 'suspended'``                            -> ``SUBSCRIPTION_SUSPENDED``
  3. ``status`` anything other than ``'active'``         -> ``EXPIRED``
  4. ``period_expiry`` null, or ``now >= period_expiry`` -> ``EXPIRED``  (Req 11.7, P-11)
  5. submission state outside the entitling three        -> ``LISTING_UNAVAILABLE``
  6. otherwise                                           -> ``SUBSCRIBED``

Step 4 is the sweep-independent expiry check: the ``status`` column alone is never the
authority, so a row still reading ``'active'`` because the housekeeping sweep is dead is
reported expired the instant ``now`` reaches its expiry. Steps 1 and 5 are answerable here
because round trip 2 embeds both the Listing and its ``marketplace_submissions`` state.

The one clause of ``resolve`` this module cannot reproduce is its final "does
``source_strategy_id`` still resolve to a live Strategy_Version" read, which is per-listing by
nature. A Listing whose backing version has vanished is therefore listed as entitling here and
refused 409 ``MARKETPLACE_STRATEGY_UNAVAILABLE`` at the moment of deployment or Paper_Session
start - which is the unavailable-strategy state Requirement 12.8 has the page render, and it is
a refusal rather than an over-grant: the resolver still runs at the admission point.

WHY A SUBSCRIBED ENTRY CARRIES A NARROWED PUBLIC PROJECTION AND NO ``creator_alias``
-----------------------------------------------------------------------------------
``listing_projection`` is the one public Listing serialiser, and
:data:`listing_projection.PUBLIC_LISTING_FIELDS` is the authority on what a Listing-derived
payload may carry. :func:`project_subscribed_listing` builds its output the same way -
empty dict, one explicit assignment per field, never a copy of the row - and closes with two
runtime checks against that authority: the key set must be a subset of
``PUBLIC_LISTING_FIELDS`` and must not meet ``DENIED_LISTING_COLUMNS``. So a column a future
migration adds to ``library_strategies`` cannot reach this response, and neither can
``author_id``, ``source_strategy_id``, the ``risk_*`` columns or the moderation columns.

It emits a strict *subset*: no ``creator_alias`` and no ``condition_summaries``.
``creator_alias`` is resolved by the batched ``profiles`` read that ``marketplace/aliases.py``
owns, and ``condition_summaries`` by the ``marketplace_backtest_evidence`` read - each a further
round trip that the Strategies_Page's fixed budget of three does not contain
(``design.md`` -> "Fixed round trips": "(1) ``strategies`` … (2) ``library_subscriptions`` with
an embedded PostgREST resource on ``library_strategies`` … so the Listing projection fields
arrive in the same request; (3) ``paper_sessions`` running-count grouped by strategy").
Omitting a field the request did not read is the honest answer; resolving it per entry would
reintroduce the N+1 that Requirement 27.2 exists to forbid. ``project_listing`` itself is not
called for exactly this reason: it *requires* a server-resolved ``creator_alias`` and refuses a
blank or fabricated one, and fabricating one to satisfy the signature is what Requirement 28.2
forbids.

No Protected_Logic column is reachable from here at all: the embed's column list is
:data:`listing_projection.LISTING_SELECT`, which names no ``blueprint``, ``graph_json``,
``buy_logic``, ``sell_logic``, ``risk``, ``indicators``, ``ml_model_path`` or model parameter -
so the owner's definition is not merely unprojected, it is never fetched.

WHY "run_backtest WHERE THE LISTING PERMITS IT" READS ``source_cloning_enabled``
-------------------------------------------------------------------------------
Requirement 12.3 qualifies ``run_backtest`` with "where the Listing permits it", and the schema
records exactly one Listing-level owner consent for a subscriber operating on the owner's
definition: ``library_strategies.source_cloning_enabled`` (Requirement 7.4, added by
migration 007, defaulted ``FALSE``). A subscriber-run backtest reports the owner's strategy's
per-condition behaviour over a window the subscriber chooses, which is a disclosure of the
definition's behaviour rather than merely its execution - the same thing that toggle governs.
Inventing a second column, or defaulting the affordance to "permitted" because no column says
otherwise, would each be a fabricated permission (Requirement 28.2). The predicate is named
once, in :data:`BACKTEST_CONSENT_COLUMN`, so a future dedicated column replaces it in one place.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* The reads. The route owns the three ``.execute()`` calls; this module owns the column lists
  they use and the derivation over their results.
* The 403s of Requirement 12.7. Those are the restricted routes' own, asserted by
  ``tests/test_subscriber_restricted_operations.py`` (task 17.5).
* The rendering of Requirement 12.5's expired state and 12.8's loading/empty/error states.
  Those are the page's; this module supplies the ``entitling`` flag, the refusal code and the
  execution-action-free ``allowed_actions`` they are rendered from.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from backend_app.backend.marketplace import entitlement_resolver as _entitlement_resolver
from backend_app.backend.marketplace import listing_projection as _listing_projection
from backend_app.backend.marketplace import money as _money
from backend_app.backend.marketplace.entitlement_resolver import (
    WIRE_CODE_FOR_REASON,
    EntitlementReason,
)
from backend_app.backend.marketplace.subscription_state import (
    STATUS_TEXT_FOR_STATE,
    SubscriptionState,
    normalise_subscription_state,
)

__all__ = [
    "OWNERSHIP_OWNED",
    "OWNERSHIP_SUBSCRIBED",
    "SUBSCRIBER_PERMITTED_ACTIONS",
    "SUBSCRIBER_FORBIDDEN_ACTIONS",
    "SUBSCRIBER_EXECUTION_ACTIONS",
    "OWNER_ACTIONS",
    "RENEWAL_ENABLED",
    "RENEWAL_CANCELLED",
    "BACKTEST_CONSENT_COLUMN",
    "ENTITLING_SUBMISSION_STATES",
    "OWNED_STRATEGY_SELECT",
    "SUBSCRIPTION_SELECT",
    "RUNNING_PAPER_SESSION_SELECT",
    "RUNNING_SESSION_STATE",
    "SUBSCRIBED_LISTING_FIELDS",
    "allowed_actions_for_owned",
    "allowed_actions_for_subscribed",
    "subscription_view",
    "entitlement_reason",
    "project_subscribed_listing",
    "running_session_counts",
    "build_my_strategies_entries",
]


# ══════════════════════════════════════════════════════════════════════════
# THE TWO OWNERSHIP LABELS (Requirement 12.2)
# ══════════════════════════════════════════════════════════════════════════

#: An entry the caller owns - a row of their own ``strategies`` table.
OWNERSHIP_OWNED: str = "OWNED"
#: An entry the caller reaches through a Subscription to somebody else's Listing.
OWNERSHIP_SUBSCRIBED: str = "SUBSCRIBED"


# ══════════════════════════════════════════════════════════════════════════
# THE ACTION VOCABULARY (Requirements 12.3, 12.4)
# ══════════════════════════════════════════════════════════════════════════

#: The eight actions a ``SUBSCRIBED`` entry may offer, in the order Requirement 12.3 lists
#: them. This is the *upper bound*: :func:`allowed_actions_for_subscribed` never emits an
#: action outside it, so an action added to the page cannot appear on a subscribed entry
#: without being added here first.
SUBSCRIBER_PERMITTED_ACTIONS: Tuple[str, ...] = (
    "view_listing",
    "run_backtest",
    "deploy_live",
    "start_paper",
    "view_performance",
    "view_subscription",
    "renew",
    "cancel_renewal",
)

#: The thirteen actions a ``SUBSCRIBED`` entry may never offer (Requirement 12.4). Every one of
#: them either mutates the owner's strategy or discloses its Protected_Logic. Named explicitly
#: rather than derived, so the requirement's list and this list can be read against each other,
#: and asserted disjoint from :data:`SUBSCRIBER_PERMITTED_ACTIONS` at import.
SUBSCRIBER_FORBIDDEN_ACTIONS: FrozenSet[str] = frozenset(
    {
        "edit",
        "open_in_builder",
        "view_graph",
        "edit_blocks",
        "view_indicator_params",
        "edit_indicator_params",
        "view_risk_config",
        "edit_risk_config",
        "export_definition",
        "download_definition",
        "view_model_params",
        "re_version",
        "delete",
    }
)

#: The subset of :data:`SUBSCRIBER_PERMITTED_ACTIONS` that *executes* the owner's strategy.
#: Requirement 12.5 disables every one of them when the Subscription does not entitle, while
#: still offering renewal - which is why they are named as a set rather than special-cased.
SUBSCRIBER_EXECUTION_ACTIONS: FrozenSet[str] = frozenset(
    {"run_backtest", "deploy_live", "start_paper"}
)

#: Every existing action, which is what an ``OWNED`` entry may offer (``design.md`` -> the
#: ``ownership`` table: "``OWNED`` | every existing action"). Derived from the two sets above so
#: an action added to either is offered to the owner automatically and cannot be forgotten here.
OWNER_ACTIONS: Tuple[str, ...] = SUBSCRIBER_PERMITTED_ACTIONS + tuple(
    sorted(SUBSCRIBER_FORBIDDEN_ACTIONS)
)

# The two sets that describe a subscriber's affordances must not overlap. Stated at import so an
# edit that moves an action into the permitted list without removing it from the forbidden one
# fails immediately and loudly.
if SUBSCRIBER_FORBIDDEN_ACTIONS & frozenset(SUBSCRIBER_PERMITTED_ACTIONS):
    raise AssertionError(
        "an action cannot be both permitted and forbidden for a SUBSCRIBED entry: "
        f"{sorted(SUBSCRIBER_FORBIDDEN_ACTIONS & frozenset(SUBSCRIBER_PERMITTED_ACTIONS))}"
    )

if not SUBSCRIBER_EXECUTION_ACTIONS <= frozenset(SUBSCRIBER_PERMITTED_ACTIONS):
    raise AssertionError(
        "every execution action must be one of the permitted subscriber actions: "
        f"{sorted(SUBSCRIBER_EXECUTION_ACTIONS - frozenset(SUBSCRIBER_PERMITTED_ACTIONS))}"
    )


# ══════════════════════════════════════════════════════════════════════════
# THE RENEWAL STATE (Requirement 12.6)
# ══════════════════════════════════════════════════════════════════════════

#: ``library_subscriptions.renewal_enabled`` is true - the Subscription is set to renew.
RENEWAL_ENABLED: str = "ENABLED"
#: ``renewal_enabled`` is false - the purchaser cancelled renewal (Requirement 11.9). Entitlement
#: still runs to the unchanged expiry; only the renewal is off.
RENEWAL_CANCELLED: str = "CANCELLED"

#: The column the renewal state is read from. ``renewal_enabled`` and NOT ``auto_renew``:
#: ``008_marketplace_settlement.sql``'s header records that ``auto_renew`` exists in only one of
#: the two shapes this table has in the wild, while ``renewal_enabled BOOLEAN NOT NULL DEFAULT
#: TRUE`` is added by that migration and therefore exists in both.
RENEWAL_COLUMN: str = "renewal_enabled"


# ══════════════════════════════════════════════════════════════════════════
# THE PERSISTED VOCABULARY THIS MODULE COMPARES AGAINST
# ══════════════════════════════════════════════════════════════════════════

# Taken from the one enum -> column mapping rather than lowercased inline, which is how the
# enum and the column drift apart (subscription_state.py's own note on STATUS_TEXT_FOR_STATE).
_STATUS_ACTIVE: str = STATUS_TEXT_FOR_STATE[SubscriptionState.ACTIVE]
_STATUS_SUSPENDED: str = STATUS_TEXT_FOR_STATE[SubscriptionState.SUSPENDED]

#: The submission states under which a Listing is still usable by an ACTIVE Subscription. A
#: ``SUSPENDED`` or ``UNPUBLISHED`` Listing does not revoke an already-paid, unexpired
#: Subscription (Requirement 4.11). Held equal to the Entitlement_Resolver's own set at import,
#: because a page that offered ``deploy_live`` on a Listing the resolver refuses - or withheld it
#: on one the resolver admits - would be exactly the client/server disagreement Requirement 12.2
#: and property P-16 exist to prevent.
ENTITLING_SUBMISSION_STATES: FrozenSet[str] = frozenset(
    {"PUBLISHED", "SUSPENDED", "UNPUBLISHED"}
)

if ENTITLING_SUBMISSION_STATES != _entitlement_resolver._ENTITLING_SUBMISSION_STATES:
    raise AssertionError(
        "the Strategies_Page's entitling submission states have drifted from the "
        "Entitlement_Resolver's: "
        f"{sorted(ENTITLING_SUBMISSION_STATES ^ _entitlement_resolver._ENTITLING_SUBMISSION_STATES)}"
    )

#: The Listing column that records the owner's consent to a subscriber operating on the
#: definition, and therefore the predicate behind Requirement 12.3's "run backtest where the
#: Listing permits it". Named once - see the module docstring for why this column and not an
#: invented one.
BACKTEST_CONSENT_COLUMN: str = "source_cloning_enabled"


# ══════════════════════════════════════════════════════════════════════════
# THE THREE PROJECTIONS (Requirement 27.2 - three round trips, entry-count independent)
# ══════════════════════════════════════════════════════════════════════════

#: Round trip 1: the caller's own strategies. An explicit column list, never ``select("*")``, and
#: it names no Protected_Logic column - ``buy_logic``, ``sell_logic``, ``risk``, ``indicators``
#: and ``ml_model_path`` are the owner's own and still have no business travelling on a list
#: page (the same reasoning ``routers/strategies.py::_LIST_COLUMNS`` records).
#: ``archived_at`` is 005a's soft-delete marker: ``NULL`` means active, and Requirement 12.1's
#: list is the active one.
OWNED_STRATEGY_SELECT: str = (
    "id,name,description,symbol,timeframe,status,is_active,"
    "created_at,updated_at,archived_at"
)

#: The ``archived_at`` filter value. Kept as a constant so the query, the column list and the
#: docstring cannot disagree about which state is listed.
ARCHIVED_AT_COLUMN: str = "archived_at"

#: Round trip 2: the caller's Subscriptions, with the Listing and its submission state arriving
#: as PostgREST embedded resources in the *same* request - which is what makes the entry count
#: irrelevant to the round-trip count (Requirement 27.2). ``!inner`` drops a Subscription whose
#: Listing no longer exists rather than emitting an entry with a null Listing.
#:
#: The embed's column list is :data:`listing_projection.LISTING_SELECT` itself, so the one
#: canonical Listing column list is the one this read requests; a column added there is
#: requested here, and a column removed there stops being requested here.
SUBSCRIPTION_SELECT: str = (
    "id,library_id,status,period_expiry,renewal_enabled,"
    "library_strategies!inner("
    + _listing_projection.LISTING_SELECT
    + ",marketplace_submissions(submission_state)"
    ")"
)

#: Round trip 3: the caller's running Paper_Sessions. Counted per strategy in Python from one
#: read (PostgREST has no ``GROUP BY``), which is one round trip whatever the entry count. The
#: set is bounded anyway by ``MAX_CONCURRENT_PAPER_SESSIONS_PER_USER`` (Requirement 27.4).
RUNNING_PAPER_SESSION_SELECT: str = "id,listing_id,source_strategy_id,session_state"

#: The ``paper_sessions.session_state`` value that counts as running (migration 009's
#: ``chk_paper_session_state``).
RUNNING_SESSION_STATE: str = "RUNNING"


# ══════════════════════════════════════════════════════════════════════════
# THE SUBSCRIBED ENTRY'S LISTING PROJECTION
# ══════════════════════════════════════════════════════════════════════════

#: The public Listing fields a ``SUBSCRIBED`` entry carries. A strict subset of
#: :data:`listing_projection.PUBLIC_LISTING_FIELDS` - see the module docstring on why
#: ``creator_alias`` and ``condition_summaries`` are absent. Declared so a test can read the
#: intended key set without executing the projection, and asserted a subset at import.
SUBSCRIBED_LISTING_FIELDS: FrozenSet[str] = frozenset(
    {
        "listing_id",
        "name",
        "description",
        "category",
        "difficulty",
        "tags",
        "supported_timeframes",
        "symbol",
        "exchange_id",
        "market_type",
        "performance_summary",
        "risk_metrics",
        "max_drawdown_pct",
        "condition_count",
        "validation_status",
        "price_minor",
        "price_display",
        "currency",
        "subscription_period_days",
        "published_at",
        "subscriber_count",
        "avg_rating",
        "rating_count",
        "source_cloning_enabled",
    }
)

if not SUBSCRIBED_LISTING_FIELDS <= _listing_projection.PUBLIC_LISTING_FIELDS:
    raise AssertionError(
        "a SUBSCRIBED entry's Listing fields must be a subset of the one public allow-list; "
        "these are not in it: "
        f"{sorted(SUBSCRIBED_LISTING_FIELDS - _listing_projection.PUBLIC_LISTING_FIELDS)}"
    )

if SUBSCRIBED_LISTING_FIELDS & _listing_projection.DENIED_LISTING_COLUMNS:
    raise AssertionError(
        "a SUBSCRIBED entry's Listing fields name denied column(s): "
        f"{sorted(SUBSCRIBED_LISTING_FIELDS & _listing_projection.DENIED_LISTING_COLUMNS)}"
    )


def project_subscribed_listing(row: Any) -> Dict[str, Any]:
    """Return the public view of one subscribed Listing, built key by key.

    Same discipline as :func:`listing_projection.project_listing`: start from an empty dict and
    perform one explicit assignment per allow-listed field, so a column a future migration adds
    to ``library_strategies`` reaches this response only if somebody writes a line here that
    puts it there (Requirement 6.1 applied to the Strategies_Page). The row is never copied,
    never mutated and nothing is deleted from it.

    Args:
        row: The embedded ``library_strategies`` row from round trip 2.

    Returns:
        A fresh ``dict`` whose key set is a subset of :data:`SUBSCRIBED_LISTING_FIELDS`, and
        therefore of :data:`listing_projection.PUBLIC_LISTING_FIELDS`.

    Raises:
        AssertionError: The projection produced a key outside the allow-list, or one the public
            projection denies. Raised explicitly rather than through ``assert`` so ``python -O``
            cannot strip the check.
    """
    out: Dict[str, Any] = {}

    # ── Identity and descriptive metadata ────────────────────────────────
    out["listing_id"] = _read(row, "id")
    out["name"] = _read(row, "name")
    out["description"] = _read(row, "description")
    out["category"] = _read(row, "category")
    out["difficulty"] = _read(row, "difficulty")
    # A fresh list, so a caller mutating the response cannot reach into the driver's row.
    out["tags"] = list(_read(row, "tags") or ())

    # ── Asset and market information ─────────────────────────────────────
    out["supported_timeframes"] = _supported_timeframes(row)
    out["symbol"] = _read(row, "symbol")
    out["exchange_id"] = _read(row, "exchange_id")
    out["market_type"] = _read(row, "market_type")

    # ── Outcome figures only, read and not recomputed (Requirement 6.3) ──
    out["performance_summary"] = {
        "total_return_pct": _read(row, "backtest_total_return_pct"),
        "win_rate_pct": _read(row, "backtest_win_rate_pct"),
        "profit_factor": _read(row, "backtest_profit_factor"),
        "total_trades": _read(row, "backtest_total_trades"),
    }
    out["risk_metrics"] = {
        "sharpe_ratio": _read(row, "backtest_sharpe_ratio"),
        "max_drawdown_pct": _read(row, "backtest_max_drawdown_pct"),
    }
    out["max_drawdown_pct"] = _read(row, "backtest_max_drawdown_pct")
    out["condition_count"] = _read(row, "condition_count")
    out["validation_status"] = _read(row, "verification_status")

    # ── Price, currency and period (Requirements 8.12, 8.13) ─────────────
    # price_minor is the exact integer; price_display goes through money.to_major, the one
    # sanctioned presentation boundary, and is a str - so no float is constructed. A row whose
    # price_minor or currency is not admissible has its display OMITTED rather than approximated
    # (Requirement 28.5), and the exact integer still travels, so one malformed row cannot take
    # the whole page down and no fabricated amount is ever shown.
    price_minor = _read(row, "price_minor")
    currency = _read(row, "currency")
    out["price_minor"] = price_minor
    display = _price_display(price_minor, currency)
    if display is not None:
        out["price_display"] = display
    out["currency"] = currency
    out["subscription_period_days"] = _listing_projection.SUBSCRIPTION_PERIOD_NOMINAL_DAYS

    # ── Publication and social counters ──────────────────────────────────
    out["published_at"] = _read(row, "published_at")
    out["subscriber_count"] = _read(row, "subscriber_count")
    out["source_cloning_enabled"] = bool(_read(row, BACKTEST_CONSENT_COLUMN))

    # ── Ratings: present only when the infrastructure holds values (Req 6.9) ──
    avg_rating = _read(row, "avg_rating")
    rating_count = _read(row, "rating_count")
    try:
        rating_count = None if rating_count is None else int(rating_count)
    except (TypeError, ValueError):
        rating_count = None
    if avg_rating is not None and rating_count:
        out["avg_rating"] = avg_rating
        out["rating_count"] = rating_count

    # The guards, not comments. Two set comparisons per entry, always on.
    surplus = set(out) - SUBSCRIBED_LISTING_FIELDS
    if surplus:
        raise AssertionError(
            "subscribed-listing projection produced fields outside the allow-list: "
            f"{sorted(surplus)}"
        )
    leaked = set(out) & _listing_projection.DENIED_LISTING_COLUMNS
    if leaked:
        raise AssertionError(
            f"subscribed-listing projection produced denied column(s): {sorted(leaked)}"
        )
    return out


# ══════════════════════════════════════════════════════════════════════════
# THE SUBSCRIPTION VIEW (Requirement 12.6)
# ══════════════════════════════════════════════════════════════════════════


def subscription_view(subscription_row: Any, now: datetime) -> Dict[str, Any]:
    """The ``{state, period_expiry, renewal_state}`` triple Requirement 12.6 displays.

    Exactly three keys, always the same three, so the page can render the Subscription_State,
    the period expiry and the renewal state without inferring any of them.

    Each value is ``None`` when the persisted row does not carry it - an unrecognised ``status``
    spelling, an absent ``period_expiry``, an absent ``renewal_enabled``. ``None`` reads as
    "unavailable", never as a default: Requirement 28.5 forbids substituting a zero, a default
    or a previous value presented as current, and "renewal is on" is precisely the kind of
    default that must not be guessed.

    Args:
        subscription_row: One ``library_subscriptions`` row from round trip 2.
        now: The instant the view is rendered at, UTC. Accepted for signature symmetry with
            :func:`entitlement_reason`; the triple itself is time-independent.

    Returns:
        ``{"state": <uppercase state or None>, "period_expiry": <ISO-8601 text or None>,
        "renewal_state": <"ENABLED" | "CANCELLED" | None>}``.
    """
    state = normalise_subscription_state(_read(subscription_row, "status"))
    expiry = _coerce_instant(_read(subscription_row, "period_expiry"))
    renewal = _read(subscription_row, RENEWAL_COLUMN)

    if renewal is None:
        renewal_state: Optional[str] = None
    else:
        renewal_state = RENEWAL_ENABLED if bool(renewal) else RENEWAL_CANCELLED

    return {
        "state": state.value if state is not None else None,
        "period_expiry": expiry.isoformat() if expiry is not None else None,
        "renewal_state": renewal_state,
    }


# ══════════════════════════════════════════════════════════════════════════
# THE ENTITLEMENT DECISION, MIRRORED (Requirements 4.11, 7.10, 11.7, 12.5)
# ══════════════════════════════════════════════════════════════════════════


def entitlement_reason(
    subscription_row: Any, listing_row: Any, now: datetime
) -> EntitlementReason:
    """The Entitlement_Resolver's reason for this Subscription, decided from the batch row.

    Re-states ``entitlement_resolver.resolve``'s decision ordering over data round trip 2
    already carries, in the resolver's own vocabulary - see the module docstring for the six
    steps and for the one clause (the Strategy_Version resolution) that is per-listing by
    nature and therefore still evaluated at the admission point.

    Args:
        subscription_row: One ``library_subscriptions`` row.
        listing_row: Its embedded ``library_strategies`` row, with the embedded
            ``marketplace_submissions`` state.
        now: The instant to compare ``period_expiry`` against, UTC. Passed in so the
            sweep-independent expiry check is deterministic (Requirement 11.7, property P-11).

    Returns:
        An :class:`EntitlementReason`. Only ``SUBSCRIBED`` entitles.
    """
    if listing_row is None:
        return EntitlementReason.LISTING_UNAVAILABLE

    status = _normalise_status(_read(subscription_row, "status"))

    # A suspended Subscription is its own answer, distinct from a lapse (Requirement 4.11).
    if status == _STATUS_SUSPENDED:
        return EntitlementReason.SUBSCRIPTION_SUSPENDED

    # Anything that is not 'active' is a lapse (Requirement 7.10's expired half).
    if status != _STATUS_ACTIVE:
        return EntitlementReason.EXPIRED

    # The sweep-independent expiry check (Requirement 11.7, P-11): the status column alone is
    # never trusted, so a null or past expiry is non-entitling even while status reads 'active'.
    expiry = _coerce_instant(_read(subscription_row, "period_expiry"))
    if expiry is None or now >= expiry:
        return EntitlementReason.EXPIRED

    # A SUSPENDED or UNPUBLISHED Listing still entitles an ACTIVE, unexpired Subscription
    # (Requirement 4.11); only a state outside the three, or no submission at all, does not.
    if _listing_submission_state(listing_row) not in ENTITLING_SUBMISSION_STATES:
        return EntitlementReason.LISTING_UNAVAILABLE

    return EntitlementReason.SUBSCRIBED


# ══════════════════════════════════════════════════════════════════════════
# THE ACTION DERIVATION (Requirements 12.3, 12.4, 12.5)
# ══════════════════════════════════════════════════════════════════════════


def allowed_actions_for_owned() -> List[str]:
    """Every existing action - what an ``OWNED`` entry may offer.

    The owner's own strategy carries no marketplace restriction: ``design.md``'s ``ownership``
    table reads "``OWNED`` | every existing action | —". Returns a fresh list so a caller cannot
    mutate the module's tuple.
    """
    return list(OWNER_ACTIONS)


def allowed_actions_for_subscribed(
    entitling: bool,
    backtest_permitted: bool,
    renewal_state: Optional[str],
) -> List[str]:
    """The action list a ``SUBSCRIBED`` entry may offer, in Requirement 12.3's order.

    Args:
        entitling: Whether the Subscription entitles right now - i.e. whether
            :func:`entitlement_reason` returned ``SUBSCRIBED``. When it does not, every
            execution action is withheld and renewal is still offered (Requirement 12.5).
        backtest_permitted: Whether the Listing permits a subscriber-run backtest, the
            "where the Listing permits it" qualifier of Requirement 12.3. Only consulted when
            ``entitling`` - a lapsed Subscription runs nothing whatever the Listing permits.
        renewal_state: The Subscription's renewal state. ``cancel_renewal`` is offered only
            while renewal is actually on; offering it against an already-cancelled renewal
            would be an affordance for a no-op.

    Returns:
        A fresh list, ordered as :data:`SUBSCRIBER_PERMITTED_ACTIONS` orders it so two entries
        in the same state produce byte-identical lists.

    Raises:
        AssertionError: The derivation produced an action outside
            :data:`SUBSCRIBER_PERMITTED_ACTIONS`, or one in
            :data:`SUBSCRIBER_FORBIDDEN_ACTIONS`. Raised explicitly rather than through
            ``assert`` so ``python -O`` cannot strip Requirement 12.4's guarantee.
    """
    selected = {"view_listing", "view_performance", "view_subscription", "renew"}

    if renewal_state == RENEWAL_ENABLED:
        selected.add("cancel_renewal")

    if entitling:
        selected.add("deploy_live")
        selected.add("start_paper")
        if backtest_permitted:
            selected.add("run_backtest")

    actions = [action for action in SUBSCRIBER_PERMITTED_ACTIONS if action in selected]

    forbidden = set(actions) & SUBSCRIBER_FORBIDDEN_ACTIONS
    if forbidden:
        raise AssertionError(
            "a SUBSCRIBED entry's allowed_actions must never contain "
            f"{sorted(forbidden)} (Requirement 12.4)"
        )
    surplus = set(actions) - set(SUBSCRIBER_PERMITTED_ACTIONS)
    if surplus:
        raise AssertionError(
            "a SUBSCRIBED entry's allowed_actions must be within the eight permitted "
            f"actions; these are not: {sorted(surplus)} (Requirement 12.3)"
        )
    if not entitling and set(actions) & SUBSCRIBER_EXECUTION_ACTIONS:
        raise AssertionError(
            "a non-entitling Subscription must offer no execution action "
            f"(Requirement 12.5); it offered {sorted(set(actions) & SUBSCRIBER_EXECUTION_ACTIONS)}"
        )
    return actions


# ══════════════════════════════════════════════════════════════════════════
# THE PAPER-SESSION COUNT (round trip 3)
# ══════════════════════════════════════════════════════════════════════════


def running_session_counts(
    session_rows: Optional[Sequence[Any]],
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Running Paper_Session counts keyed by strategy and by Listing.

    One pass over the caller's running sessions, which is what makes the count per entry free of
    a per-entry read (Requirement 27.2). Two maps because the two kinds of entry are keyed
    differently: an owned entry is a ``strategies.id``, which a session records as
    ``source_strategy_id``; a subscribed entry is a ``library_strategies.id``, which a session
    records as ``listing_id``. Keying a subscribed entry by ``listing_id`` also means the
    owner's ``source_strategy_id`` never has to travel to the subscriber.

    Args:
        session_rows: The running ``paper_sessions`` rows, or ``None`` when the read did not
            complete - in which case both maps are empty and the caller omits the figure rather
            than reporting a zero it did not measure (Requirement 28.5).

    Returns:
        ``(by_source_strategy_id, by_listing_id)``.
    """
    by_strategy: Dict[str, int] = {}
    by_listing: Dict[str, int] = {}
    for row in session_rows or ():
        if _normalise_state(_read(row, "session_state")) != RUNNING_SESSION_STATE:
            continue
        strategy_id = _as_text(_read(row, "source_strategy_id"))
        if strategy_id:
            by_strategy[strategy_id] = by_strategy.get(strategy_id, 0) + 1
        listing_id = _as_text(_read(row, "listing_id"))
        if listing_id:
            by_listing[listing_id] = by_listing.get(listing_id, 0) + 1
    return by_strategy, by_listing


# ══════════════════════════════════════════════════════════════════════════
# THE COMBINED LIST (Requirements 12.1, 12.2, 12.3, 12.4, 12.6)
# ══════════════════════════════════════════════════════════════════════════


def build_my_strategies_entries(
    caller_id: Any,
    owned_rows: Optional[Sequence[Any]],
    subscription_rows: Optional[Sequence[Any]],
    session_rows: Optional[Sequence[Any]],
    now: datetime,
) -> List[Dict[str, Any]]:
    """The Strategies_Page's combined owned-and-subscribed list, from the three read results.

    One list, each entry labelled and each entry carrying the actions it permits
    (Requirements 12.1, 12.2, 12.3, 12.4, 12.6). Owned entries come first, then subscribed ones;
    within each group the caller's ordering (the query's ``ORDER BY``) is preserved, so the
    response order is a property of the reads rather than of a re-sort here.

    A Subscription to the caller's *own* Listing is skipped: the strategy behind it is already
    in the list as an ``OWNED`` entry, and emitting it twice would put two different action sets
    on one strategy. ``author_id`` is read for that comparison and never projected.

    Args:
        caller_id: The authenticated caller's id. Identity comes from the server-side session
            only (Requirements 7.7, 21.1) - nothing here reads a body, query or path value.
        owned_rows: Round trip 1's ``strategies`` rows.
        subscription_rows: Round trip 2's ``library_subscriptions`` rows, each with its embedded
            Listing.
        session_rows: Round trip 3's running ``paper_sessions`` rows, or ``None`` when that read
            did not complete.
        now: The instant to decide entitlement at, UTC.

    Returns:
        The list of entries. Each carries ``ownership``, ``allowed_actions`` and - for a
        subscribed entry - ``subscription`` and ``entitling``.
    """
    by_strategy, by_listing = running_session_counts(session_rows)
    counts_known = session_rows is not None

    entries: List[Dict[str, Any]] = []

    # ── OWNED (round trip 1) ─────────────────────────────────────────────
    for row in owned_rows or ():
        strategy_id = _as_text(_read(row, "id"))
        entry: Dict[str, Any] = {
            "entry_id": f"owned:{strategy_id}",
            "ownership": OWNERSHIP_OWNED,
            "strategy_id": strategy_id,
            "name": _read(row, "name"),
            "description": _read(row, "description"),
            "symbol": _read(row, "symbol"),
            "timeframe": _read(row, "timeframe"),
            "status": _read(row, "status"),
            "is_active": _read(row, "is_active"),
            "created_at": _read(row, "created_at"),
            "updated_at": _read(row, "updated_at"),
            "subscription": None,
            "entitling": True,
            "unavailable_reason": None,
            "allowed_actions": allowed_actions_for_owned(),
        }
        if counts_known:
            entry["running_paper_sessions"] = by_strategy.get(strategy_id or "", 0)
        entries.append(entry)

    # ── SUBSCRIBED (round trip 2) ────────────────────────────────────────
    for row in subscription_rows or ():
        listing = _embedded_listing(row)
        if listing is None:
            # ``!inner`` should make this unreachable; a Subscription with no Listing is not an
            # entry that can be rendered, and inventing one would be fabricated data.
            continue
        if _same_id(_read(listing, "author_id"), caller_id):
            continue

        listing_id = _as_text(_read(listing, "id")) or _as_text(_read(row, "library_id"))
        reason = entitlement_reason(row, listing, now)
        entitling = reason is EntitlementReason.SUBSCRIBED
        view = subscription_view(row, now)

        entry = {
            "entry_id": f"subscribed:{_as_text(_read(row, 'id'))}",
            "ownership": OWNERSHIP_SUBSCRIBED,
            "listing_id": listing_id,
            "subscription_id": _as_text(_read(row, "id")),
            "listing": project_subscribed_listing(listing),
            "subscription": view,
            "entitling": entitling,
            # The stable code the server would refuse this entry's execution actions with, or
            # ``None`` while it entitles. Read from the Entitlement_Resolver's own mapping so the
            # page's expired/unavailable state and the eventual 403/409 name the same condition.
            "unavailable_reason": WIRE_CODE_FOR_REASON[reason],
            "allowed_actions": allowed_actions_for_subscribed(
                entitling=entitling,
                backtest_permitted=bool(_read(listing, BACKTEST_CONSENT_COLUMN)),
                renewal_state=view["renewal_state"],
            ),
        }
        if counts_known:
            entry["running_paper_sessions"] = by_listing.get(listing_id or "", 0)
        entries.append(entry)

    return entries


# ══════════════════════════════════════════════════════════════════════════
# SMALL PURE UTILITIES
# ══════════════════════════════════════════════════════════════════════════


def _read(obj: Any, key: str) -> Any:
    """Read ``key`` from a mapping or an attribute off an object. ``None`` when absent."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _as_row_list(value: Any) -> List[Any]:
    """A PostgREST embedded resource as a list. Handles a single mapping and ``None``."""
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if item is not None]
    return [value]


def _embedded_listing(subscription_row: Any) -> Any:
    """The ``library_strategies`` resource embedded on one Subscription row, or ``None``.

    PostgREST returns a to-one embed as an object, but a client double (and a to-many reading of
    the same relationship) may return a single-element array, so both shapes are accepted.
    """
    embedded = _read(subscription_row, "library_strategies")
    rows = _as_row_list(embedded)
    return rows[0] if rows else None


def _listing_submission_state(listing_row: Any) -> Optional[str]:
    """The Listing's submission-lifecycle state, upper-cased, or ``None`` when there is none.

    Same reading as the Entitlement_Resolver's: a Listing may carry more than one submission row
    over its life (a resubmission after a rejection), so the state is an entitling one if ANY
    embedded submission is in the entitling set, and otherwise the first row's state.
    """
    states = [
        _normalise_state(_read(item, "submission_state"))
        for item in _as_row_list(_read(listing_row, "marketplace_submissions"))
    ]
    states = [state for state in states if state]
    if not states:
        return None
    for state in states:
        if state in ENTITLING_SUBMISSION_STATES:
            return state
    return states[0]


def _supported_timeframes(row: Any) -> List[str]:
    """The Listing's timeframes, falling back to the single tested ``timeframe``.

    Same rule as :func:`listing_projection._supported_timeframes`: a Listing published before the
    ``supported_timeframes TEXT[]`` column existed carries ``NULL`` for it while still carrying
    the ``timeframe`` it was backtested on. Reporting that one timeframe is the truthful narrower
    answer; inventing a wider set would claim support the evidence does not show.
    """
    declared = _read(row, "supported_timeframes")
    if declared:
        return list(declared)
    timeframe = _read(row, "timeframe")
    return [timeframe] if timeframe else []


def _price_display(price_minor: Any, currency: Any) -> Optional[str]:
    """The price as a major-unit string, or ``None`` when it cannot be rendered exactly.

    Goes through :func:`money.to_major`, the sanctioned presentation boundary, and returns a
    ``str`` - so no ``float`` is constructed here and none can be constructed from this field
    downstream (Requirements 8.12, 8.13). A ``price_minor`` that is not an admissible integer, or
    a currency with no persisted exponent, yields ``None`` so the caller omits the field rather
    than showing an approximation (Requirement 28.5).
    """
    if price_minor is None or currency is None:
        return None
    try:
        return str(_money.to_major(price_minor, currency))
    except Exception:
        # money.MoneyError and anything else a malformed row produces: the exact price_minor
        # still travels, and no approximate amount is invented in its place.
        return None


def _as_text(value: Any) -> Optional[str]:
    """``value`` as text, or ``None`` when it is ``None``. Ids may be UUID objects."""
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
    """An upper-cased state token, or ``None``."""
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _coerce_instant(value: Any) -> Optional[datetime]:
    """A timestamp value as a tz-aware UTC datetime, or ``None``.

    Accepts a ``datetime`` (naive is read as UTC) and an ISO-8601 string (the shape a JSON driver
    returns). Anything else, and an unparseable string, is ``None`` - which the entitlement check
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
