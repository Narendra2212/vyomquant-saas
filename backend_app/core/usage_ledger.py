"""
core/usage_ledger.py — where a plan limit meets the truth.

TWO KINDS OF LIMIT, TWO MECHANISMS
==================================
:mod:`core.subscription_engine` says what a plan allows. This module answers what an account has
actually used, and it answers it two different ways because the two kinds of limit have two
different sources of truth:

**Counted resources** — active strategies, live strategies, exchange connections, active ML
models, marketplace listings, marketplace subscriptions — are *point-in-time facts about rows
that exist*. They are counted from the persistence layer at the moment of the check. A
maintained counter is the wrong instrument here: it drifts the first time a row is deleted by a
path that forgot to decrement, by an admin, or by a foreign-key cascade, and a drifted counter
either locks a paying customer out of capacity they own or hands them capacity they do not.
The table cannot drift from itself.

**Metered resources** — backtests, optimizations, ML training runs — are *consumption over a
calendar month*. There is no row to count: the whole point is that the run happened, whatever
became of its output. These accumulate in a period-scoped meter and are **never given back on
completion**, which is precisely what stops "delete the model and train again" from being a way
to reset the month.

WHY THERE IS A DURABLE LEDGER BEHIND THE REDIS METER
===================================================
Redis holds the hot counter because a reservation must be atomic and must not cost a database
round trip on every backtest. But Redis is a cache: it can be flushed, evicted or replaced, and
if that were the only record then a restart would silently hand every account a fresh month.
So every successful reservation also appends a row to ``plan_usage_ledger`` (migration 017), and
when a meter key is absent the meter is REBUILT from that table before it is trusted. The
ledger is the record; Redis is the fast path over it.

``idempotency_key`` on that table is what makes a retry safe. A client that retries a request,
or a gateway that redelivers, presents the same key and the unique index refuses the second
insert — so the second attempt cannot bill the account twice for one piece of work.

FAILING CLOSED, AND WHERE IT IS ALLOWED NOT TO
==============================================
In production, a count that could not be read is not evidence of an empty account. Enforcement
therefore raises rather than assuming zero, exactly as
:func:`core.subscription_dependencies.get_user_plan` already does for the plan itself. Outside
production the read degrades to zero so that local development without Supabase still runs.
"""

from __future__ import annotations

import inspect
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from backend_app.core.cache import redis_manager
from backend_app.core.subscription_engine import (
    COUNTED_RESOURCES,
    METERED_RESOURCES,
    PER_PARENT_RESOURCES,
    Resource,
    SubscriptionEngine,
    usage_period,
)

logger = logging.getLogger("UsageLedger")


class UsageReadFailed(RuntimeError):
    """A usage figure could not be established.

    Raised instead of returning zero, because zero is a claim ("this account uses nothing") that
    a failed read does not support. Callers map this onto a 503, never onto permission to
    proceed.
    """


def _is_production() -> bool:
    return os.getenv("ENV", "").lower() == "production"


#: The strategy states this platform treats as "live" — the same set
#: ``routers/exchange.py`` already uses when it decides whether a venue has running bots.
_LIVE_STRATEGY_STATES: Tuple[str, ...] = ("running", "deployed", "active")

#: The deployment states that occupy capacity. A stopped or failed deployment does not.
_LIVE_DEPLOYMENT_STATES: Tuple[str, ...] = ("deploying", "running", "paused")

#: Marketplace listing moderation states that occupy a publishing slot.
_PUBLISHED_LISTING_STATES: Tuple[str, ...] = ("approved", "featured")


def _rows(response: Any) -> List[Dict[str, Any]]:
    """The row list off a supabase response, or ``[]`` when it carries none."""
    data = getattr(response, "data", None)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


async def _execute(query: Any) -> Any:
    """Run a supabase query whether the installed client is sync or async.

    The codebase supports both shapes and checks with ``inspect.isawaitable`` at every call
    site; this is that check, once.
    """
    result = query.execute()
    if inspect.isawaitable(result):
        return await result
    return result


# ══════════════════════════════════════════════════════════════════════════
# Counted resources
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CountSpec:
    """How one counted resource is counted.

    Declarative so the mapping from "a plan limit" to "a query against the real schema" is
    readable in one place and testable without a database.
    """

    resource: str
    table: str
    user_column: str = "user_id"
    #: ``column -> value`` equality filters.
    equals: Tuple[Tuple[str, Any], ...] = ()
    #: ``column -> allowed values`` membership filters.
    within: Tuple[Tuple[str, Tuple[Any, ...]], ...] = ()
    #: Columns that must be NULL — the soft-delete predicate.
    is_null: Tuple[str, ...] = ()
    #: The column whose distinct values are counted. ``id`` counts rows.
    distinct_on: str = "id"
    #: When set, the resource is scoped to a parent row (``strategy_versions`` per strategy).
    parent_column: Optional[str] = None
    #: A human sentence naming what this count is, used verbatim in refusals and in the API.
    describes: str = ""


#: Every counted resource, and the query that establishes it.
#:
#: ``STRATEGIES`` is "not archived" because ``005a_strategy_archive.sql`` makes
#: ``archived_at IS NULL`` the platform's own definition of a live strategy row. ``BOTS`` is
#: counted from TWO sources and unioned on strategy id, because this platform has two deploy
#: paths — ``strategies.status`` (set by ``POST /api/strategies/{id}/deploy``) and
#: ``strategy_deployments`` (written by the strategy-operations and marketplace deploy paths) —
#: and a limit that only saw one of them would be trivially bypassable through the other.
_COUNT_SPECS: Dict[str, Tuple[CountSpec, ...]] = {
    Resource.STRATEGIES.value: (
        CountSpec(
            resource=Resource.STRATEGIES.value,
            table="strategies",
            is_null=("archived_at",),
            describes="active strategies",
        ),
    ),
    Resource.BOTS.value: (
        CountSpec(
            resource=Resource.BOTS.value,
            table="strategies",
            within=(("status", _LIVE_STRATEGY_STATES),),
            is_null=("archived_at",),
            distinct_on="id",
            describes="live strategies",
        ),
        CountSpec(
            resource=Resource.BOTS.value,
            table="strategy_deployments",
            equals=(("environment", "live"),),
            within=(("status", _LIVE_DEPLOYMENT_STATES),),
            distinct_on="strategy_id",
            describes="live strategies",
        ),
    ),
    Resource.PAPER_STRATEGIES.value: (
        CountSpec(
            resource=Resource.PAPER_STRATEGIES.value,
            table="strategy_deployments",
            equals=(("environment", "paper"),),
            within=(("status", _LIVE_DEPLOYMENT_STATES),),
            distinct_on="strategy_id",
            describes="paper strategies",
        ),
    ),
    Resource.EXCHANGE_CONNECTIONS.value: (
        CountSpec(
            resource=Resource.EXCHANGE_CONNECTIONS.value,
            table="exchange_keys",
            distinct_on="exchange_id",
            describes="exchange connections",
        ),
    ),
    Resource.TRADING_ACCOUNTS.value: (
        CountSpec(
            resource=Resource.TRADING_ACCOUNTS.value,
            table="paper_accounts",
            distinct_on="id",
            describes="trading accounts",
        ),
    ),
    Resource.ML_MODELS.value: (
        CountSpec(
            resource=Resource.ML_MODELS.value,
            table="model_versions",
            equals=(("is_active", True),),
            distinct_on="id",
            describes="active ML models",
        ),
    ),
    Resource.MARKETPLACE_PUBLISHED.value: (
        CountSpec(
            resource=Resource.MARKETPLACE_PUBLISHED.value,
            table="library_strategies",
            user_column="author_id",
            equals=(("is_active", True),),
            within=(("moderation_status", _PUBLISHED_LISTING_STATES),),
            distinct_on="id",
            describes="marketplace listings",
        ),
    ),
    Resource.MARKETPLACE_SUBSCRIPTIONS.value: (
        CountSpec(
            resource=Resource.MARKETPLACE_SUBSCRIPTIONS.value,
            table="library_subscriptions",
            within=(("status", ("active", "cancelled")),),
            distinct_on="library_id",
            describes="marketplace subscriptions",
        ),
    ),
    Resource.STRATEGY_VERSIONS.value: (
        CountSpec(
            resource=Resource.STRATEGY_VERSIONS.value,
            table="strategy_versions",
            # strategy_versions carries no user_id: RLS reaches the owner through the parent
            # strategy, and so does this count. The parent id is therefore mandatory, and the
            # caller must have already established that the parent belongs to the user.
            user_column="",
            parent_column="strategy_id",
            distinct_on="id",
            describes="versions of this strategy",
        ),
    ),
}

#: A counted resource with no persistence to count.
#:
#: ``custom_indicators`` is in the published plan table but this platform has no per-account
#: custom-indicator store: indicators are a static code registry
#: (``backend/indicators_backend.AVAILABLE_INDICATORS``) plus a JSONB configuration array on each
#: strategy row. There is therefore no set of owned objects to count and no creation endpoint to
#: gate. The limit is carried in the catalogue so it is auditable and so the API reports it, and
#: this constant is what makes the absence explicit rather than silently reporting zero usage
#: forever. :func:`count` refuses to invent a figure for it.
UNCOUNTABLE_RESOURCES: Tuple[str, ...] = (Resource.CUSTOM_INDICATORS.value,)

#: Why each uncountable resource cannot be counted. Surfaced to the API so a client renders the
#: reason rather than a fabricated ``0 / 3``.
UNCOUNTABLE_REASONS: Dict[str, str] = {
    Resource.CUSTOM_INDICATORS.value: (
        "Custom indicators are not stored as per-account objects in this platform, so there is "
        "no count to report."
    ),
}


async def _count_one(spec: CountSpec, user_id: str, supabase: Any, parent_id: Optional[str]) -> set:
    """The distinct ids one :class:`CountSpec` matches."""
    query = supabase.table(spec.table).select(spec.distinct_on)
    if spec.user_column:
        query = query.eq(spec.user_column, user_id)
    if spec.parent_column:
        if not parent_id:
            raise UsageReadFailed(
                f"{spec.resource} is scoped to a parent row but no parent id was supplied"
            )
        query = query.eq(spec.parent_column, parent_id)
    for column, value in spec.equals:
        query = query.eq(column, value)
    for column, allowed in spec.within:
        query = query.in_(column, list(allowed))
    for column in spec.is_null:
        query = query.is_(column, "null")

    response = await _execute(query)
    if getattr(response, "error", None):
        raise UsageReadFailed(f"{spec.table} read returned an error: {response.error!r}")
    return {row.get(spec.distinct_on) for row in _rows(response) if row.get(spec.distinct_on) is not None}


async def count(
    resource: str,
    user_id: str,
    supabase: Any,
    parent_id: Optional[str] = None,
) -> int:
    """How many units of a COUNTED ``resource`` the account currently holds.

    Raises :class:`UsageReadFailed` when the figure cannot be established in production, and
    when the resource has no persistence to count at all. Never returns a guess.
    """
    if resource in UNCOUNTABLE_RESOURCES:
        raise UsageReadFailed(UNCOUNTABLE_REASONS[resource])

    specs = _COUNT_SPECS.get(resource)
    if not specs:
        raise UsageReadFailed(f"{resource} is not a counted resource")

    if supabase is None:
        if _is_production():
            raise UsageReadFailed(
                f"Persistence layer unavailable; the {resource} count could not be established."
            )
        return 0

    try:
        identifiers: set = set()
        for spec in specs:
            identifiers |= await _count_one(spec, user_id, supabase, parent_id)
        return len(identifiers)
    except UsageReadFailed:
        raise
    except Exception as exc:
        logger.error("Failed to count %s for user %s: %s", resource, user_id, exc)
        if _is_production():
            raise UsageReadFailed(
                f"The {resource} count could not be established, so the request was not "
                f"permitted to proceed."
            ) from exc
        return 0


# ══════════════════════════════════════════════════════════════════════════
# Metered resources
# ══════════════════════════════════════════════════════════════════════════

_LEDGER_TABLE = "plan_usage_ledger"


async def _ledger_total(
    resource: str,
    user_id: str,
    supabase: Any,
    period: str,
) -> Optional[int]:
    """The durable total for one user/resource/period, or ``None`` if it cannot be read.

    ``None`` rather than ``0`` so the caller can tell "the ledger says nothing was used" from
    "the ledger could not be consulted" — the two demand different behaviour and collapsing them
    is how a cache flush turns into free capacity.
    """
    if supabase is None:
        return None
    try:
        response = await _execute(
            supabase.table(_LEDGER_TABLE)
            .select("amount")
            .eq("user_id", user_id)
            .eq("resource", resource)
            .eq("period", period)
        )
        if getattr(response, "error", None):
            return None
        total = 0
        for row in _rows(response):
            try:
                total += int(row.get("amount") or 0)
            except (TypeError, ValueError):
                continue
        return total
    except Exception as exc:
        logger.warning(
            "Usage ledger read failed for %s/%s/%s: %s", user_id, resource, period, exc
        )
        return None


async def _append_ledger(
    resource: str,
    user_id: str,
    supabase: Any,
    period: str,
    amount: int,
    idempotency_key: str,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a reservation durably. Best effort: Redis already holds the decision.

    A duplicate ``idempotency_key`` is expected and is not an error — it is the unique index
    doing its job on a retry — so it is logged at debug and swallowed.
    """
    if supabase is None:
        return
    try:
        await _execute(
            supabase.table(_LEDGER_TABLE).insert(
                {
                    "user_id": user_id,
                    "resource": resource,
                    "period": period,
                    "amount": int(amount),
                    "idempotency_key": idempotency_key,
                    "context": context or {},
                }
            )
        )
    except Exception as exc:
        logger.debug(
            "Usage ledger append skipped for %s/%s/%s (%s): %s",
            user_id,
            resource,
            period,
            idempotency_key,
            exc,
        )


async def _reconcile_meter(
    resource: str,
    user_id: str,
    supabase: Any,
    period: str,
) -> None:
    """Seed the Redis meter from the durable ledger when the key is absent.

    Called before a reservation, and only when Redis holds no key for this period — which is
    either a genuinely fresh month or a cache that lost its contents. Consulting the ledger makes
    those two cases behave differently, which is the whole reason the ledger exists.
    """
    key = SubscriptionEngine._meter_key(user_id, resource, period)
    try:
        existing = await redis_manager.get(key)
    except Exception as exc:
        logger.warning("Meter read failed for %s: %s", key, exc)
        return
    if existing is not None:
        return

    durable = await _ledger_total(resource, user_id, supabase, period)
    if durable is None or durable <= 0:
        return
    try:
        await redis_manager.set(key, str(durable), ex=SubscriptionEngine._METER_TTL_SECONDS)
        logger.info(
            "Rebuilt %s meter for user %s period %s from the durable ledger: %d",
            resource,
            user_id,
            period,
            durable,
        )
    except Exception as exc:
        logger.warning("Meter rebuild write failed for %s: %s", key, exc)


async def usage(
    resource: str,
    user_id: str,
    supabase: Any = None,
    period: Optional[str] = None,
) -> int:
    """The current reading for a METERED ``resource``.

    Prefers the Redis meter and falls back to the durable ledger when the meter is empty, so a
    reported figure is never lower than what was actually recorded.
    """
    active_period = period or usage_period()
    meter = await SubscriptionEngine.get_quota_usage(user_id, resource, active_period)
    if meter > 0:
        return meter
    durable = await _ledger_total(resource, user_id, supabase, active_period)
    return durable if durable and durable > meter else meter


async def reserve(
    resource: str,
    user_id: str,
    plan_key: str,
    supabase: Any = None,
    amount: int = 1,
    limit: Optional[int] = None,
    idempotency_key: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, int, int]:
    """Reserve monthly capacity for a metered resource.

    Returns ``(allowed, usage, limit)``. The reservation is atomic in Redis and durable in the
    ledger. It is taken BEFORE the work runs and is not returned when the work fails, because a
    failed backtest consumed the same compute a successful one would have — and because
    "fail, retry, repeat" would otherwise be an unlimited allowance.
    """
    active_period = usage_period()
    await _reconcile_meter(resource, user_id, supabase, active_period)

    allowed, current, effective = await SubscriptionEngine.reserve_quota(
        user_id,
        plan_key,
        resource,
        amount=amount,
        limit=limit,
        period=active_period,
    )

    if allowed:
        await _append_ledger(
            resource,
            user_id,
            supabase,
            active_period,
            amount,
            idempotency_key or f"{resource}:{user_id}:{uuid.uuid4().hex}",
            context,
        )
    return allowed, current, effective


async def release(
    resource: str,
    user_id: str,
    supabase: Any = None,
    amount: int = 1,
    idempotency_key: Optional[str] = None,
) -> int:
    """Return a reservation whose work never started.

    Only for the case where the request was refused downstream before any compute was spent
    (a validation failure after the reservation, say). A completed run — successful or failed —
    is never released.
    """
    active_period = usage_period()
    remaining = await SubscriptionEngine.decrement_quota_usage(
        user_id, resource, amount=amount, period=active_period
    )
    if supabase is not None and idempotency_key:
        try:
            await _execute(
                supabase.table(_LEDGER_TABLE)
                .delete()
                .eq("user_id", user_id)
                .eq("resource", resource)
                .eq("period", active_period)
                .eq("idempotency_key", idempotency_key)
            )
        except Exception as exc:
            logger.debug("Usage ledger release skipped for %s: %s", idempotency_key, exc)
    return remaining


# ══════════════════════════════════════════════════════════════════════════
# The whole picture, for the entitlements endpoint and the billing page
# ══════════════════════════════════════════════════════════════════════════


async def collect_usage(
    user_id: str,
    supabase: Any,
    resources: Optional[Sequence[str]] = None,
    strict: bool = False,
) -> Tuple[Dict[str, int], Dict[str, str]]:
    """Every usage figure for a user, plus the reasons any of them is missing.

    Returns ``(usage, unavailable)``. A resource appears in exactly one of the two: a figure that
    could not be established is NOT reported as zero, because the billing page renders a usage
    bar off these numbers and ``0 / 10`` is a claim rather than an absence.

    ``strict`` is for the enforcement path, where an unreadable figure must raise. The reporting
    path passes ``False``: a billing page that cannot read one tile should still render the
    other eleven.
    """
    wanted = tuple(resources) if resources else tuple(r.value for r in Resource)
    figures: Dict[str, int] = {}
    unavailable: Dict[str, str] = {}

    for resource in wanted:
        if resource in PER_PARENT_RESOURCES:
            # Per-strategy by definition; there is no single account-level figure to report.
            unavailable[resource] = (
                "Strategy versions are limited per strategy, so there is no single "
                "account-wide figure."
            )
            continue
        if resource in UNCOUNTABLE_RESOURCES:
            unavailable[resource] = UNCOUNTABLE_REASONS[resource]
            continue
        try:
            if resource in METERED_RESOURCES:
                figures[resource] = await usage(resource, user_id, supabase)
            elif resource in COUNTED_RESOURCES:
                figures[resource] = await count(resource, user_id, supabase)
        except UsageReadFailed as exc:
            if strict:
                raise
            unavailable[resource] = str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Unexpected failure collecting %s for %s: %s", resource, user_id, exc)
            if strict:
                raise UsageReadFailed(str(exc)) from exc
            unavailable[resource] = "This figure could not be read."

    return figures, unavailable


async def has_active_model(
    user_id: str,
    supabase: Any,
    version_id: str,
    node_id: str,
) -> bool:
    """Whether ``(version_id, node_id)`` already holds an active model for this user.

    Exposed because the ML model limit needs to know WHETHER a training run would ADD a model or
    REPLACE one: ``uq_mv_active_per_node`` keeps at most one active model per node, so retraining a
    node that already has one leaves the account's model count unchanged.
    """
    if supabase is None:
        if _is_production():
            raise UsageReadFailed(
                "Persistence layer unavailable; the active model for this node could not be read."
            )
        return False
    try:
        response = await _execute(
            supabase.table("model_versions")
            .select("id")
            .eq("user_id", user_id)
            .eq("version_id", version_id)
            .eq("node_id", node_id)
            .eq("is_active", True)
        )
        if getattr(response, "error", None):
            raise UsageReadFailed(
                f"model_versions read returned an error: {response.error!r}"
            )
        return len(_rows(response)) > 0
    except UsageReadFailed:
        raise
    except Exception as exc:
        logger.error(
            "Failed to read the active model for %s/%s/%s: %s", user_id, version_id, node_id, exc
        )
        if _is_production():
            raise UsageReadFailed(
                "The active model for this node could not be read, so the request was not "
                "permitted to proceed."
            ) from exc
        return False


async def exchange_ids(user_id: str, supabase: Any) -> set:
    """The venue ids the account already holds credentials for, lower-cased.

    Exposed separately from :func:`count` because the exchange-connection limit needs to know
    WHICH venues are connected, not just how many: re-storing keys for a venue already connected
    is a rotation that consumes no new slot.
    """
    spec = _COUNT_SPECS[Resource.EXCHANGE_CONNECTIONS.value][0]
    if supabase is None:
        if _is_production():
            raise UsageReadFailed(
                "Persistence layer unavailable; the exchange connection list could not be read."
            )
        return set()
    try:
        identifiers = await _count_one(spec, user_id, supabase, None)
    except UsageReadFailed:
        raise
    except Exception as exc:
        logger.error("Failed to read exchange connections for %s: %s", user_id, exc)
        if _is_production():
            raise UsageReadFailed(
                "The exchange connection list could not be read, so the request was not "
                "permitted to proceed."
            ) from exc
        return set()
    return {str(value).strip().lower() for value in identifiers if value is not None}


def describes(resource: str) -> str:
    """The human phrase for a resource, for use in refusal copy."""
    specs = _COUNT_SPECS.get(resource)
    if specs and specs[0].describes:
        return specs[0].describes
    return {
        Resource.BACKTESTS.value: "backtests this month",
        Resource.OPTIMIZATIONS.value: "optimization runs this month",
        Resource.ML_TRAININGS.value: "ML training runs this month",
        Resource.CUSTOM_INDICATORS.value: "custom indicators",
    }.get(resource, resource.replace("_", " "))
