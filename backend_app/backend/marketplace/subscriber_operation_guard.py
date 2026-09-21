"""
backend_app/backend/marketplace/subscriber_operation_guard.py — the server half of
Requirement 12.7, built on ``design.md`` → "Server-side artifact resolution".

Spec: marketplace-subscriptions-paper-trading task 17.9.
Requirements 7.1, 7.8, 7.9, 7.12, 12.1-12.7, 21.4, 23.2, 23.3.

THE DEFECT THIS MODULE CLOSES
-----------------------------
Fourteen restricted operations on a subscribed strategy answered **404 "not found"** to a caller
holding an ``ACTIVE`` Subscription to the Listing that owns it. Every one of them is a read or an
``UPDATE`` filtered ``.eq("id", …).eq("user_id", caller)`` whose empty result was reported as
non-existence. For a subscriber that is a *false* statement: they can see the strategy on their
own Strategies page, served by ``GET /api/library/my-strategies`` as a ``SUBSCRIBED`` entry.
``design.md`` → "Server-side artifact resolution" states the fix in one sentence:

    *"…the change is to make the refusal a 403 with a stable code rather than a 404, per
    Requirement 12.7, for the specific case where the caller holds an ``ACTIVE`` Subscription to
    the Listing that owns the strategy."*

THE DISTINCTION IS THREE-WAY, NOT TWO-WAY
-----------------------------------------
====================  ======================================================================
Caller                Answer
====================  ======================================================================
**owner**             unchanged, exactly as before this module existed
**entitled           403 ``MARKETPLACE_OPERATION_NOT_PERMITTED`` + one
subscriber**          ``MARKETPLACE_ACCESS_REFUSED`` Audit_Log entry (Requirements 7.12, 12.7)
**unrelated          **still 404**, byte-identical to a non-existent artifact (Requirement
stranger**            21.4)
====================  ======================================================================

The stranger's 404 is the reason the handlers are shaped the way they are, and it is NOT a
defect: Requirement 21.4 requires a response "byte-identical in shape and content to the response
returned for an identifier that exists in no tenant". Converting the stranger's 404 into a 403
would turn every one of these endpoints into an existence oracle — a caller could enumerate other
tenants' strategy ids by reading the status code. So this module refuses **only** on the
entitling ``SUBSCRIBED`` verdict and leaves every other verdict's answer exactly as it was.

WHY THE CHECK RUNS *AFTER* THE OWNER-SCOPED READ, NOT INSTEAD OF IT
-------------------------------------------------------------------
Each call site invokes this module at the point where it was **already about to refuse** — the
``if not resp.data`` branch, the ``except ValueError``, the ``except ArchiveRejected`` naming
``STRATEGY_NOT_FOUND``. Three consequences, all of them the point:

1. **An owner's behaviour cannot change.** The owner-scoped predicate is untouched and still
   answers first; a request that used to succeed never reaches this module at all. Nothing here
   removes or loosens an ``.eq("user_id", …)`` filter, and no RLS policy or tenant scope is
   relaxed — the new resolution runs *beside* the old predicate, never in place of it.
2. **No cost on the happy path.** An owner's read issues exactly the round trips it issued
   before. The extra reads happen only where a refusal was already being written.
3. **Nothing is mutated by the probe.** Every read here is a ``select``.

THE ENTITLEMENT DECISION IS NOT RE-IMPLEMENTED HERE
---------------------------------------------------
:func:`~backend_app.backend.marketplace.entitlement_resolver.resolve` is the single admission
decision (task 17.1), and this module *asks* it rather than deciding anything itself. There is no
second reading of ``library_subscriptions.status`` here, no second expiry comparison and no
second submission-state gate — which is what makes "the refusal agrees with what
``my-strategies`` says a ``SUBSCRIBED`` entry may do" a fact rather than a hope: task 17.4's
``allowed_actions`` and this module's :data:`RESTRICTED_OPERATIONS` are both derived from
``library_entries.SUBSCRIBER_FORBIDDEN_ACTIONS``, and :data:`_COVERED_ACTIONS` asserts at import
that the two agree **exactly**.

THE STRATEGY → LISTING CORRELATION
----------------------------------
``resolve`` takes a **Listing** id; these endpoints hold a **strategy** id.
``library_strategies.source_strategy_id`` is the link, and it is followed in ONE round trip
(:func:`_listings_for_strategy`) that selects three columns and no Protected_Logic. Requirement
2.7 bounds a strategy to one live Listing, so the loop over the returned rows is a loop over one
row in every well-formed database; it is written as a loop rather than a ``.single()`` so a
historical unpublished Listing beside a live one cannot make the decision depend on row order.

A READ THAT DID NOT COMPLETE DOES NOT MANUFACTURE A VERDICT
-----------------------------------------------------------
:class:`~backend_app.backend.marketplace.entitlement_resolver.EntitlementReadFailed` — and any
other failure of the correlation read — leaves the call site's **pre-existing** refusal in place,
logged at ``error``. It deliberately does not become a 503, for a reason worth stating plainly:
this module is a *refinement* of a refusal that some other read, which DID complete, has already
decided. A broken marketplace read cannot establish that the caller is a subscriber, so it cannot
justify a 403; and turning it into a 503 would convert every 404 on fourteen live endpoints into
a dependency error the moment one marketplace column is missing — a far larger blast radius than
the defect being fixed, on a path where the caller is being refused either way. No success is
fabricated and no numeric field is defaulted (Requirements 1.5, 1.7): the answer is the same
refusal the handler had already chosen.

NOTHING IN A REFUSAL DISCLOSES PROTECTED_LOGIC OR THE OWNER
-----------------------------------------------------------
The 403 body is the shared catalogue's own sentence for
``MARKETPLACE_OPERATION_NOT_PERMITTED`` plus ``details`` carrying the *operation name* and the
**caller's own** Listing id. No graph, node, indicator, threshold, model path, version document,
``author_id`` or owner email is read into this module, let alone returned: the correlation read
selects ``id``, ``author_id`` and ``source_strategy_id``, and ``author_id`` is used only to be
compared — it never reaches a response or an audit entry.
``listing_projection.DENIED_LISTING_COLUMNS`` and ``tests/property/
test_protected_logic_containment.py``'s P-47 are the authority; the Audit_Log entry is written
through ``library._audit_marketplace_refusal``, the same writer task 17.3's clone and deploy
refusals use, so there is one entry shape rather than two.

The Requirement 12.4 *action names* a route performs are deliberately kept out of both the body
and the entry — ``view_indicator_params`` contains the substring ``indicator``, which is a
Protected_Logic token of any strategy whose DAG names an indicator, and the containment oracle
matches by substring. See :func:`_refuse`.

WHY THIS MODULE REACHES INTO ``routers/library.py`` FOR TWO SEAMS
-----------------------------------------------------------------
:func:`_service_client` and the audit writer are imported from ``backend_app.routers.library``
**inside** the functions that use them, not at module scope. The lazy import is what keeps the
import graph acyclic (``routers/strategies.py`` imports this module; ``routers/library.py``
imports a great deal of the marketplace package). Reaching for library's accessors rather than
building new ones is deliberate: there is ONE service-role client accessor and ONE
``MARKETPLACE_ACCESS_REFUSED`` writer in this codebase, and a second copy of either would be a
second thing to keep in step with the first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, FrozenSet, List, Mapping, Optional
from uuid import UUID

from backend_app.backend.marketplace import entitlement_resolver as _resolver
from backend_app.backend.marketplace.errors import (
    MARKETPLACE_OPERATION_NOT_PERMITTED,
    MarketplaceError,
)
from backend_app.backend.marketplace.library_entries import (
    SUBSCRIBER_FORBIDDEN_ACTIONS,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RestrictedOperation",
    "RESTRICTED_OPERATIONS",
    "STRATEGY_READ",
    "STRATEGY_UPDATE",
    "STRATEGY_RENAME",
    "STRATEGY_ARCHIVE",
    "VERSION_HISTORY",
    "VERSION_COMPARE",
    "VERSION_RESTORE",
    "NODE_PREVIEW",
    "MODEL_ARTIFACT_DOWNLOAD",
    "refuse_if_entitled_subscriber",
    "refuse_if_entitled_subscriber_for_model_version",
]

#: The catalogue code every refusal in this module carries, re-exported so a call site can name
#: it without importing ``errors.py`` twice. Requirement 12.7 names this code.
REFUSAL_CODE = MARKETPLACE_OPERATION_NOT_PERMITTED


# ══════════════════════════════════════════════════════════════════════════
# THE RESTRICTED OPERATIONS (Requirement 12.4's thirteen, mapped onto routes)
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RestrictedOperation:
    """One route that performs one or more of Requirement 12.4's forbidden actions.

    ``name`` is what the Audit_Log entry records as the attempted operation, and ``actions`` is
    the set of Requirement 12.4 action names that route actually performs. The two are separate
    because this repository's strategy surface is **coarse-grained**: one ``PUT
    /api/strategies/{id}`` edits the graph, the indicator parameters and the risk configuration
    together, and one ``GET`` returns all three. A single refusal is the correct answer for every
    action the route carries, and recording the action set is what makes that auditable rather
    than implied.
    """

    #: The operation name recorded in the Audit_Log entry's ``metadata.operation``.
    name: str
    #: The Requirement 12.4 actions this route performs. Every member must be in
    #: :data:`~backend_app.backend.marketplace.library_entries.SUBSCRIBER_FORBIDDEN_ACTIONS`.
    actions: FrozenSet[str]

    def __post_init__(self) -> None:
        """Hold this operation to task 17.4's list, at construction rather than by review."""
        if not self.actions:
            raise ValueError(f"RestrictedOperation({self.name!r}) names no forbidden action")
        stray = self.actions - SUBSCRIBER_FORBIDDEN_ACTIONS
        if stray:
            raise ValueError(
                f"RestrictedOperation({self.name!r}) names action(s) that Requirement 12.4 does "
                f"not forbid: {sorted(stray)}"
            )


#: ``GET /api/strategies/{id}``. The row carries the lifted DAG fields, the ``risk`` column and
#: the ``ml_model_path``, so this one read *is* opening the Builder, reading the risk
#: configuration and downloading the definition — there is no narrower route for any of the
#: three.
STRATEGY_READ = RestrictedOperation(
    name="strategy_read",
    actions=frozenset({"open_in_builder", "view_risk_config", "download_definition"}),
)

#: ``PUT /api/strategies/{id}``. One write covers the four edit actions.
STRATEGY_UPDATE = RestrictedOperation(
    name="strategy_update",
    actions=frozenset({"edit", "edit_blocks", "edit_indicator_params", "edit_risk_config"}),
)

#: ``PUT /api/strategies/{id}/rename`` — the dedicated rename route Requirement 7.8 names.
STRATEGY_RENAME = RestrictedOperation(name="strategy_rename", actions=frozenset({"edit"}))

#: ``DELETE /api/strategies/{id}`` — a soft archive, which is this platform's delete.
STRATEGY_ARCHIVE = RestrictedOperation(name="strategy_archive", actions=frozenset({"delete"}))

#: ``GET /api/strategies/{id}/versions`` — each version carries its blueprint, so the history is
#: the graph, per version.
VERSION_HISTORY = RestrictedOperation(name="version_history", actions=frozenset({"view_graph"}))

#: ``POST /api/strategies/{id}/versions/compare`` — two blueprints in one response is an export
#: of the definition in all but name.
VERSION_COMPARE = RestrictedOperation(
    name="version_compare", actions=frozenset({"export_definition"})
)

#: ``POST /api/strategies/{id}/versions/restore`` — mints a NEW version of the owner's strategy.
VERSION_RESTORE = RestrictedOperation(
    name="version_restore", actions=frozenset({"re_version"})
)

#: ``POST /api/strategy-operations/strategies/{id}/nodes/{node_id}/preview`` — evaluates one of
#: the owner's nodes and returns its indicator values and parameters.
NODE_PREVIEW = RestrictedOperation(
    name="node_preview", actions=frozenset({"view_indicator_params"})
)

#: ``GET /api/strategy-operations/models/{model_version_id}/download`` — the bound ML model's
#: artifact and its hyperparameters.
MODEL_ARTIFACT_DOWNLOAD = RestrictedOperation(
    name="model_artifact_download", actions=frozenset({"view_model_params"})
)


#: Every restricted operation this module guards, by name. Declared so the set can be read
#: against Requirement 12.4's list in one place, and so a test can enumerate it.
RESTRICTED_OPERATIONS: Mapping[str, RestrictedOperation] = MappingProxyType(
    {
        operation.name: operation
        for operation in (
            STRATEGY_READ,
            STRATEGY_UPDATE,
            STRATEGY_RENAME,
            STRATEGY_ARCHIVE,
            VERSION_HISTORY,
            VERSION_COMPARE,
            VERSION_RESTORE,
            NODE_PREVIEW,
            MODEL_ARTIFACT_DOWNLOAD,
        )
    }
)

#: The union of every guarded route's action set. Asserted EQUAL to Requirement 12.4's thirteen
#: below: "your refusals must agree with ``my-strategies``'s ``allowed_actions`` exactly" is a
#: claim about set equality, so it is stated as one. An action added to Requirement 12.4 without
#: a route to refuse it here fails at import, and a route naming an action the requirement does
#: not forbid fails in :meth:`RestrictedOperation.__post_init__`.
_COVERED_ACTIONS: FrozenSet[str] = frozenset(
    action for operation in RESTRICTED_OPERATIONS.values() for action in operation.actions
)

if _COVERED_ACTIONS != SUBSCRIBER_FORBIDDEN_ACTIONS:
    raise AssertionError(
        "the guarded routes and Requirement 12.4's forbidden-action list disagree.\n"
        f"  forbidden but unguarded: {sorted(SUBSCRIBER_FORBIDDEN_ACTIONS - _COVERED_ACTIONS)}\n"
        f"  guarded but not forbidden: {sorted(_COVERED_ACTIONS - SUBSCRIBER_FORBIDDEN_ACTIONS)}"
    )


# ══════════════════════════════════════════════════════════════════════════
# THE READ PROJECTIONS — three columns, and no Protected_Logic in either
# ══════════════════════════════════════════════════════════════════════════

#: The strategy → Listing correlation. ``source_strategy_id`` is the link ``design.md`` names.
#: ``author_id`` is selected to be *compared* (never returned, never audited); no logic column,
#: no ``moderation_notes``, no ``evaluation_score`` — nothing from
#: ``listing_projection.DENIED_LISTING_COLUMNS`` reaches a caller through this module.
_LISTING_CORRELATION_SELECT = "id,author_id,source_strategy_id"

#: The model-version → strategy correlation, for the one guarded route whose path names a model
#: version rather than a strategy. Two columns, neither of them the artifact URI, the checksum or
#: the hyperparameters.
_MODEL_VERSION_CORRELATION_SELECT = "id,strategy_id"


# ══════════════════════════════════════════════════════════════════════════
# THE GUARD
# ══════════════════════════════════════════════════════════════════════════


async def refuse_if_entitled_subscriber(
    user: Any,
    *,
    strategy_id: Any,
    operation: RestrictedOperation,
    service_client: Any = None,
    now: Optional[datetime] = None,
) -> None:
    """Refuse ``operation`` with 403 when ``user`` holds an entitling Subscription to it.

    Call this at the point a handler was **already about to refuse** a caller for whom the
    owner-scoped predicate matched nothing. It returns ``None`` — leaving that pre-existing
    refusal to stand — for every caller except one: the holder of an ``ACTIVE``, unexpired
    Subscription to a Listing whose ``source_strategy_id`` is ``strategy_id``.

    Args:
        user: the authenticated server-side identity. Only ``id`` is read; no identifier from a
            body, query, path or WS message participates in the decision (Requirements 7.7,
            21.1).
        strategy_id: the strategy the operation was attempted against.
        operation: which of :data:`RESTRICTED_OPERATIONS` was attempted.
        service_client: the service-role Persistence_Layer handle. Injected in tests; resolved
            from ``routers/library.py``'s single accessor when omitted.
        now: the instant ``period_expiry`` is compared against. Injected so the
            sweep-independent expiry check (Requirement 11.7) is deterministic under test.

    Returns:
        ``None`` — for an owner, for a stranger, for a lapsed or suspended subscriber, and
        whenever the decision could not be read. In every one of those cases the caller receives
        the answer the handler had already chosen, unchanged.

    Raises:
        MarketplaceError: ``MARKETPLACE_OPERATION_NOT_PERMITTED`` (403) when the caller holds an
            entitling Subscription. One ``MARKETPLACE_ACCESS_REFUSED`` Audit_Log entry is written
            first, carrying the caller, the Listing, the operation and the reason code, and no
            Protected_Logic (Requirement 7.12).
    """
    caller_id = _identity(user)
    if not caller_id:
        # No authenticated identity: the route's own dependency has already refused, and there
        # is nothing to resolve a relationship against.
        return None

    strategy_key = _as_identifier(strategy_id)
    if strategy_key is None:
        # A strategy id that is not UUID-shaped matches no `source_strategy_id`, so the
        # correlation read would only produce a driver error to swallow.
        return None

    client = service_client if service_client is not None else _service_client()
    if client is None:
        # DEV_MODE with no service-role credentials configured. Nothing can be resolved, and
        # the handler's own refusal stands.
        logger.debug(
            "subscriber guard skipped for %s: no service-role client is configured",
            operation.name,
        )
        return None

    listings = _listings_for_strategy(client, strategy_key, operation=operation.name)
    if not listings:
        return None

    instant = now or datetime.now(timezone.utc)

    for listing in listings:
        listing_id = _text(listing.get("id"))
        if not listing_id:
            continue
        entitlement = await _entitlement(
            client, caller_id, listing_id, instant, operation.name
        )
        if entitlement is None:
            # The decision could not be read for this Listing. Not an answer, and not a
            # refusal — see the module docstring.
            continue
        if entitlement.reason is _resolver.EntitlementReason.OWNED:
            # The caller owns this Listing's strategy. Their answer is whatever the handler
            # already decided, byte for byte: an owner's behaviour does not change here.
            return None
        if entitlement.reason is _resolver.EntitlementReason.SUBSCRIBED:
            await _refuse(
                caller_id=caller_id,
                listing_id=listing_id,
                strategy_id=strategy_key,
                operation=operation,
            )
            # `_refuse` always raises; this is unreachable and is here so a future edit that
            # made it fall through could not silently admit the operation.
            raise AssertionError("the subscriber refusal did not raise")  # pragma: no cover

    # Stranger, lapsed subscriber, suspended subscription, unavailable Listing: unchanged.
    # In particular a stranger still receives the handler's 404, indistinguishable from the
    # answer for an identifier that exists in no tenant (Requirement 21.4).
    return None


async def refuse_if_entitled_subscriber_for_model_version(
    user: Any,
    *,
    model_version_id: Any,
    operation: RestrictedOperation = MODEL_ARTIFACT_DOWNLOAD,
    service_client: Any = None,
    now: Optional[datetime] = None,
) -> None:
    """:func:`refuse_if_entitled_subscriber`, for the route whose path names a model version.

    ``GET /api/strategy-operations/models/{model_version_id}/download`` holds no strategy id, so
    the ``model_versions`` row is read for its ``strategy_id`` first — two columns, neither the
    artifact URI nor the hyperparameters — and the strategy guard then does the rest. The read is
    service-role scoped and unfiltered by ``user_id`` on purpose: the whole point is to decide
    what a caller who is *not* the owner may be told, and a caller-scoped read of a foreign row
    returns nothing by construction.

    Returns ``None`` when the model version does not resolve to a strategy, which leaves
    ``MODEL_VERSION_NOT_FOUND`` in place — the correct answer for a model version that genuinely
    is not there.
    """
    caller_id = _identity(user)
    if not caller_id:
        return None

    model_key = _as_identifier(model_version_id)
    if model_key is None:
        return None

    client = service_client if service_client is not None else _service_client()
    if client is None:
        logger.debug(
            "subscriber guard skipped for %s: no service-role client is configured",
            operation.name,
        )
        return None

    strategy_id = _strategy_id_for_model_version(client, model_key, operation=operation.name)
    if not strategy_id:
        return None

    return await refuse_if_entitled_subscriber(
        user,
        strategy_id=strategy_id,
        operation=operation,
        service_client=client,
        now=now,
    )


# ══════════════════════════════════════════════════════════════════════════
# THE REFUSAL
# ══════════════════════════════════════════════════════════════════════════


async def _refuse(
    *,
    caller_id: str,
    listing_id: str,
    strategy_id: str,
    operation: RestrictedOperation,
) -> None:
    """Audit the refusal, then raise it. Always raises.

    The Audit_Log entry is written FIRST and through ``library._audit_marketplace_refusal`` —
    the same writer task 17.3's clone and deploy refusals use, so there is one entry shape in
    this codebase rather than two. That helper swallows its own logger failures, so an audit
    facility that is down cannot convert this 403 into a 500 and tell the caller something false
    about why they were refused.

    ``details`` carries the operation and the **caller's own** Listing id — nothing the caller
    did not already have. No ``author_id``, no owner email, no graph, node, indicator,
    threshold, version document or model path (Requirement 7.1).

    WHY THE ACTION SET IS *NOT* ON THE WIRE OR IN THE ENTRY
    ------------------------------------------------------
    :attr:`RestrictedOperation.actions` is internal vocabulary — the Requirement 12.4 action
    names this route performs, held equal to
    ``library_entries.SUBSCRIBER_FORBIDDEN_ACTIONS`` at import. Two of those names
    (``view_indicator_params``, ``edit_indicator_params``) contain the substring ``indicator``,
    and ``indicator`` is a genuine Protected_Logic token of any strategy whose DAG nodes name an
    indicator: ``listing_projection.protected_logic_tokens`` harvests mapping keys as well as
    values, and ``assert_contains_no_protected_logic`` matches by **substring**. So emitting the
    action set would put a Protected_Logic token in a refusal body and in an Audit_Log entry —
    exactly what Requirement 7.1 forbids "including error and diagnostic responses", and what
    P-47 asserts. The set is therefore kept internal: the caller and the entry get the
    ``operation`` name, which Requirement 7.12 names as "the attempted operation" and which
    carries no such collision. A client that needs to know which actions a ``SUBSCRIBED`` entry
    may perform reads ``allowed_actions`` from ``GET /api/library/my-strategies`` (task 17.4),
    which is where that list belongs.
    """
    from backend_app.routers.library import _audit_marketplace_refusal

    await _audit_marketplace_refusal(
        actor_id=caller_id,
        listing_id=listing_id,
        operation=operation.name,
        reason=MARKETPLACE_OPERATION_NOT_PERMITTED,
        extra={
            "entitlement_reason": _resolver.EntitlementReason.SUBSCRIBED.value,
            "strategy_id": strategy_id,
        },
    )
    raise MarketplaceError(
        MARKETPLACE_OPERATION_NOT_PERMITTED,
        details={
            "operation": operation.name,
            "listing_id": listing_id,
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# THE READS
# ══════════════════════════════════════════════════════════════════════════


def _listings_for_strategy(
    client: Any, strategy_id: str, *, operation: str
) -> List[Mapping[str, Any]]:
    """Every Listing backed by ``strategy_id``, in ONE round trip. ``[]`` on any failure.

    Requirement 2.7 permits one live Listing per strategy, so this is one row in a well-formed
    database; a rejected or unpublished predecessor may sit beside it, which is why the caller
    loops rather than taking ``[0]``.
    """
    try:
        response = (
            client.table("library_strategies")
            .select(_LISTING_CORRELATION_SELECT)
            .eq("source_strategy_id", strategy_id)
            .execute()
        )
        error = getattr(response, "error", None)
        if error:
            raise RuntimeError(str(error))
        return _rows(response)
    except Exception as exc:  # noqa: BLE001 — the handler's own refusal stands (see docstring)
        logger.error(
            "subscriber guard could not correlate strategy %s to a Listing for %s: %s",
            strategy_id,
            operation,
            exc,
        )
        return []


def _strategy_id_for_model_version(
    client: Any, model_version_id: str, *, operation: str
) -> Optional[str]:
    """The ``strategy_id`` behind one model version, in ONE round trip. ``None`` on any failure."""
    try:
        response = (
            client.table("model_versions")
            .select(_MODEL_VERSION_CORRELATION_SELECT)
            .eq("id", model_version_id)
            .execute()
        )
        error = getattr(response, "error", None)
        if error:
            raise RuntimeError(str(error))
        rows = _rows(response)
    except Exception as exc:  # noqa: BLE001 — the handler's own 404 stands
        logger.error(
            "subscriber guard could not correlate model version %s to a strategy for %s: %s",
            model_version_id,
            operation,
            exc,
        )
        return None
    if not rows:
        return None
    return _text(rows[0].get("strategy_id"))


async def _entitlement(
    client: Any, caller_id: str, listing_id: str, now: datetime, operation: str
):
    """One :func:`entitlement_resolver.resolve` call, or ``None`` when it could not answer.

    The resolver is the single admission decision; this wrapper adds no rule of its own. It is
    ``await``ed from the guard's own coroutine — never driven by ``asyncio.run``, which closes
    the loop it creates and would leave the request's thread without one — and the resolver is
    handed the caller identity as ``{"id": caller_id}`` so nothing but the authenticated session
    reaches the decision (Requirements 7.7, 21.1).

    A :class:`EntitlementReadFailed` is NOT converted into a verdict: it returns ``None`` and the
    handler's own refusal stands.
    """
    try:
        return await _resolver.resolve({"id": caller_id}, listing_id, client, now)
    except _resolver.EntitlementReadFailed as exc:
        logger.error(
            "subscriber guard could not resolve entitlement for listing %s (%s): %s",
            listing_id,
            operation,
            exc,
        )
        return None
    except Exception as exc:  # noqa: BLE001 — never turn a broken read into an admission
        logger.error(
            "subscriber guard entitlement resolution failed for listing %s (%s): %s",
            listing_id,
            operation,
            exc,
        )
        return None


# ══════════════════════════════════════════════════════════════════════════
# SMALL PURE UTILITIES
# ══════════════════════════════════════════════════════════════════════════


def _service_client() -> Any:
    """``routers/library.py``'s single service-role accessor, imported lazily.

    Lazy to keep the import graph acyclic, and library's rather than a new one so this codebase
    has exactly one service-role client accessor. Returns ``None`` when none is configured.
    """
    try:
        from backend_app.routers.library import _build_service_client

        return _build_service_client()
    except Exception as exc:  # noqa: BLE001 — an unconfigured client is not a caller error
        logger.error("subscriber guard could not obtain a service-role client: %s", exc)
        return None


def _identity(user: Any) -> Optional[str]:
    """The authenticated caller's id, from the server-side session and nowhere else."""
    if isinstance(user, Mapping):
        raw = user.get("id")
    else:
        raw = getattr(user, "id", None)
    return _text(raw)


def _as_identifier(value: Any) -> Optional[str]:
    """``value`` as a UUID-shaped string, or ``None``.

    The identifiers these routes carry are UUIDs. Validating the shape here is not a security
    control — every read below is a parameterised PostgREST filter, never interpolated query text
    (Requirement 22.3) — it simply avoids issuing a read that PostgreSQL would refuse with
    ``22P02`` for an identifier that could not have matched anything anyway.
    """
    text = _text(value)
    if not text:
        return None
    try:
        return str(UUID(text))
    except (ValueError, AttributeError, TypeError):
        return None


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _rows(response: Any) -> List[Mapping[str, Any]]:
    """The rows of a PostgREST response, or ``[]``. The same reading the resolver applies."""
    data = getattr(response, "data", None)
    if data is None and isinstance(response, Mapping):
        data = response.get("data")
    if data is None and isinstance(response, list):
        data = response
    if data is None:
        return []
    if isinstance(data, Mapping):
        return [data]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, Mapping)]
    return []
