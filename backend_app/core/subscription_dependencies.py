"""
core/subscription_dependencies.py — the entitlement gates every route hangs off.

WHAT THIS MODULE IS
===================
FastAPI dependencies that assert a caller's plan permits what they are about to do. It is the
ONLY enforcement path in the backend: :mod:`core.subscription_engine` says what a plan allows,
:mod:`core.usage_ledger` says what the account has used, and this module turns the two into a
403 or a pass. Nothing here trusts the client — not a header, not a body field, not a token
claim about a tier. The plan is read from ``profiles.subscription_tier`` on every request that
needs it (behind a short cache), because that column is what billing writes and therefore the
only thing that can be authoritative.

STRUCTURED REFUSALS, NOT SENTENCES
==================================
A quota refusal used to be ``403 "Quota exceeded for strategies: 3/3. Upgrade your plan."`` — a
string. A client could render it and nothing else: it could not tell which limit was hit, how
close the account was, or which plan would fix it, so every upgrade prompt in the UI had to
restate the plan ladder locally and could disagree with the server.

Refusals now carry :class:`EntitlementRefusal`, serialised as the ``detail`` of the 403:

    {
      "code": "STRATEGY_LIMIT_REACHED",
      "resource": "strategies",
      "current": 3,
      "limit": 3,
      "plan": "starter",
      "tier": "TRADER",
      "required_plan": "pro",
      "required_tier": "PRO_QUANT",
      "message": "You've reached your Trader strategy capacity.",
      "upgrade_message": "Upgrade to Pro Quant to run up to 10 strategies.",
      "cta_label": "Upgrade to Pro — ₹999",
      "contact_sales": false
    }

Every number in that payload is measured, not assumed, and the copy is generated from the one
catalogue — so the sentence a trader reads and the limit the server enforced cannot drift apart.
``contact_sales`` is what the top of the ladder sets instead of naming a higher plan, because
there isn't one to name.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional

from fastapi import Depends, HTTPException, status

from backend_app.core.cache import redis_manager
from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.core.subscription_engine import (
    COUNTED_RESOURCES,
    METERED_RESOURCES,
    Feature,
    Plan,
    Resource,
    SubscriptionEngine,
)
from backend_app.core import usage_ledger
from backend_app.core.usage_ledger import UsageReadFailed

logger = logging.getLogger("SubscriptionDependencies")


def _is_production() -> bool:
    return os.getenv("ENV", "").lower() == "production"


# ══════════════════════════════════════════════════════════════════════════
# The refusal
# ══════════════════════════════════════════════════════════════════════════

#: Refusal code per resource. Stable strings: a client branches on these, so they are part of
#: the API contract and are asserted by the test suite.
_LIMIT_CODES: Dict[str, str] = {
    Resource.STRATEGIES.value: "STRATEGY_LIMIT_REACHED",
    Resource.PAPER_STRATEGIES.value: "PAPER_STRATEGY_LIMIT_REACHED",
    Resource.BOTS.value: "LIVE_STRATEGY_LIMIT_REACHED",
    Resource.EXCHANGE_CONNECTIONS.value: "EXCHANGE_CONNECTION_LIMIT_REACHED",
    Resource.TRADING_ACCOUNTS.value: "TRADING_ACCOUNT_LIMIT_REACHED",
    Resource.BACKTESTS.value: "BACKTEST_LIMIT_REACHED",
    Resource.CUSTOM_INDICATORS.value: "CUSTOM_INDICATOR_LIMIT_REACHED",
    Resource.STRATEGY_VERSIONS.value: "STRATEGY_VERSION_LIMIT_REACHED",
    Resource.ML_MODELS.value: "ML_MODEL_LIMIT_REACHED",
    Resource.ML_TRAININGS.value: "ML_TRAINING_LIMIT_REACHED",
    Resource.OPTIMIZATIONS.value: "OPTIMIZATION_LIMIT_REACHED",
    Resource.MARKETPLACE_SUBSCRIPTIONS.value: "MARKETPLACE_SUBSCRIPTION_LIMIT_REACHED",
    Resource.MARKETPLACE_PUBLISHED.value: "MARKETPLACE_LISTING_LIMIT_REACHED",
}

#: Refusal code per feature.
_FEATURE_CODES: Dict[str, str] = {
    Feature.LIVE_TRADING.value: "LIVE_TRADING_NOT_INCLUDED",
    Feature.ML_TRAINING.value: "ML_NOT_INCLUDED",
    Feature.ML_NODES.value: "ML_NOT_INCLUDED",
    Feature.OPTIMIZATION.value: "OPTIMIZATION_NOT_INCLUDED",
    Feature.MARKETPLACE_SUBSCRIBE.value: "MARKETPLACE_SUBSCRIBE_NOT_INCLUDED",
    Feature.MARKETPLACE_PUBLISH.value: "MARKETPLACE_PUBLISH_NOT_INCLUDED",
    Feature.ADVANCED_RISK.value: "ADVANCED_RISK_NOT_INCLUDED",
}


@dataclass(frozen=True)
class FeatureCopy:
    """The sentences a locked capability shows.

    Held here, next to the refusal that raises them, so the backend and the frontend read the
    same words: the upgrade panel renders the server's ``message`` / ``upgrade_message`` rather
    than composing its own from a local plan table. ``{plan}`` is filled with the display name of
    the cheapest plan that grants the capability.
    """

    label: str
    #: Overrides the default "X is available with Y." sentence where the product has a specific
    #: phrasing. ``None`` uses the default.
    message: Optional[str] = None
    #: The verb on the button. Defaults to "Upgrade to".
    verb: str = "Upgrade to"


#: Per-feature copy. A feature absent from this table gets a sentence derived from its flag name,
#: which reads acceptably for simple flags and is why this table only carries the ones that need
#: better words.
_FEATURE_COPY: Dict[str, FeatureCopy] = {
    Feature.LIVE_TRADING.value: FeatureCopy(
        label="Live trading",
        message="Live trading is available with {plan}.",
    ),
    Feature.ML_TRAINING.value: FeatureCopy(
        label="ML Strategy Nodes",
        message="ML Strategy Nodes are available with {plan}.",
    ),
    Feature.ML_NODES.value: FeatureCopy(
        label="ML Strategy Nodes",
        message="ML Strategy Nodes are available with {plan}.",
    ),
    Feature.OPTIMIZATION.value: FeatureCopy(
        label="Parameter optimization",
        message="Parameter optimization is available with {plan}.",
    ),
    Feature.MARKETPLACE_SUBSCRIBE.value: FeatureCopy(
        label="Marketplace subscriptions",
        message="Marketplace subscriptions start with {plan}.",
    ),
    Feature.MARKETPLACE_PUBLISH.value: FeatureCopy(
        label="Marketplace publishing",
        message="Marketplace publishing starts with {plan}.",
        verb="Unlock",
    ),
    Feature.CREATOR_REVENUE.value: FeatureCopy(
        label="Creator earnings",
        message="Publishing to the marketplace and earning from subscribers starts with {plan}.",
        verb="Unlock",
    ),
    Feature.ADVANCED_RISK.value: FeatureCopy(
        label="Strategy-level risk controls",
        message="Strategy-level risk controls are available with {plan}.",
    ),
    Feature.PORTFOLIO_RISK.value: FeatureCopy(
        label="Portfolio-level risk controls",
        message="Portfolio-level risk controls are available with {plan}.",
    ),
}


def _feature_copy(feature: str) -> FeatureCopy:
    return _FEATURE_COPY.get(feature, FeatureCopy(label=feature.replace("_", " ").capitalize()))


#: The capabilities a UI surface renders a locked state for.
#:
#: Narrower than the full :class:`Feature` enum on purpose. ``GET /api/billing/entitlements`` emits
#: a refusal payload for each of these that the caller's plan lacks, so a frontend gate can render
#: the server's own words without a request per gate — and a list of every flag would bloat that
#: payload with copy for capabilities no screen gates on (``tick_data``, ``historical_data``, the
#: analytics flags). These are the ones with a lock on screen behind them.
GATEABLE_FEATURES: tuple = (
    Feature.LIVE_TRADING.value,
    Feature.ADVANCED_RISK.value,
    Feature.PORTFOLIO_RISK.value,
    Feature.OPTIMIZATION.value,
    Feature.ML_TRAINING.value,
    Feature.ML_NODES.value,
    Feature.ADVANCED_BUILDER.value,
    Feature.STRATEGY_VERSIONING.value,
    Feature.MARKETPLACE_SUBSCRIBE.value,
    Feature.MARKETPLACE_PUBLISH.value,
    Feature.CREATOR_REVENUE.value,
)

#: The published monthly rupee price, for the CTA label. Read from the catalogue so the figure in
#: an upgrade button is the figure the checkout will charge.
def _price_label(plan_id: str) -> str:
    config = SubscriptionEngine.get_plan_config(plan_id)
    if not config or config.is_custom_priced:
        return ""
    paise = config.pricing.get("INR")
    if not paise:
        return ""
    return f"₹{paise // 100:,}"


def _capacity_sentence(resource: str, limit: int) -> str:
    """``"run up to 10 strategies"`` — the phrase that completes an upgrade prompt."""
    noun = usage_ledger.describes(resource)
    if resource in METERED_RESOURCES:
        return f"run up to {limit} {noun}"
    return f"run up to {limit} {noun}"


@dataclass
class EntitlementRefusal:
    """A machine-readable refusal. Serialised as the ``detail`` of a 403."""

    code: str
    message: str
    plan: str
    tier: str
    resource: Optional[str] = None
    feature: Optional[str] = None
    current: Optional[int] = None
    limit: Optional[int] = None
    required_plan: Optional[str] = None
    required_tier: Optional[str] = None
    upgrade_message: Optional[str] = None
    cta_label: Optional[str] = None
    contact_sales: bool = False

    def as_detail(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def as_http(self) -> HTTPException:
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=self.as_detail())


def build_quota_refusal(
    resource: str,
    plan_key: str,
    current: int,
    limit: int,
) -> EntitlementRefusal:
    """The refusal for a capacity limit, with the upgrade path resolved from the catalogue."""
    config = SubscriptionEngine.get_plan_config(plan_key) or SubscriptionEngine.get_plan_config(
        Plan.FREE.value
    )
    noun = usage_ledger.describes(resource)
    code = _LIMIT_CODES.get(resource, "PLAN_LIMIT_REACHED")

    # The cheapest plan that would admit one more unit. `None` means no plan on the ladder does,
    # which is the top of the ladder and a sales conversation rather than a self-serve upgrade.
    required = SubscriptionEngine.minimum_plan_for_quota(resource, current + 1)
    if required is not None and SubscriptionEngine.plan_rank(required) <= SubscriptionEngine.plan_rank(
        config.id
    ):
        required = SubscriptionEngine.next_plan(config.id)

    message = (
        f"You've reached your {config.name} capacity for {noun}."
        if limit > 0
        else f"{noun.capitalize()} are not included in {config.name}."
    )

    if required is None:
        return EntitlementRefusal(
            code=code,
            message=message,
            plan=config.id,
            tier=config.tier,
            resource=resource,
            current=current,
            limit=limit,
            upgrade_message="Contact us for custom capacity.",
            cta_label="Talk to Sales",
            contact_sales=True,
        )

    required_config = SubscriptionEngine.get_plan_config(required)
    required_limit = SubscriptionEngine.get_quota_limit(required, resource)
    if required_config.is_custom_priced:
        return EntitlementRefusal(
            code=code,
            message=message,
            plan=config.id,
            tier=config.tier,
            resource=resource,
            current=current,
            limit=limit,
            required_plan=required_config.id,
            required_tier=required_config.tier,
            upgrade_message="Contact us for custom capacity.",
            cta_label="Talk to Sales",
            contact_sales=True,
        )

    price = _price_label(required_config.id)
    return EntitlementRefusal(
        code=code,
        message=message,
        plan=config.id,
        tier=config.tier,
        resource=resource,
        current=current,
        limit=limit,
        required_plan=required_config.id,
        required_tier=required_config.tier,
        upgrade_message=(
            f"Upgrade to {required_config.name} to "
            f"{_capacity_sentence(resource, required_limit)}."
        ),
        cta_label=f"Upgrade to {required_config.name}{f' — {price}' if price else ''}",
    )


def build_feature_refusal(feature: str, plan_key: str) -> EntitlementRefusal:
    """The refusal for a capability a plan does not include."""
    config = SubscriptionEngine.get_plan_config(plan_key) or SubscriptionEngine.get_plan_config(
        Plan.FREE.value
    )
    required = SubscriptionEngine.minimum_plan_for_feature(feature)
    copy = _feature_copy(feature)
    code = _FEATURE_CODES.get(feature, "FEATURE_NOT_INCLUDED")

    if required is None:
        return EntitlementRefusal(
            code=code,
            message=f"{copy.label} is not available on any plan.",
            plan=config.id,
            tier=config.tier,
            feature=feature,
            contact_sales=True,
            cta_label="Talk to Sales",
        )

    required_config = SubscriptionEngine.get_plan_config(required)
    price = _price_label(required_config.id)
    template = copy.message or "{label} is available with {plan}."
    return EntitlementRefusal(
        code=code,
        message=template.format(plan=required_config.name, label=copy.label),
        plan=config.id,
        tier=config.tier,
        feature=feature,
        required_plan=required_config.id,
        required_tier=required_config.tier,
        upgrade_message=f"{required_config.name} — {required_config.tagline}",
        cta_label=(
            "Talk to Sales"
            if required_config.is_custom_priced
            else f"{copy.verb} {required_config.name}{f' — {price}' if price else ''}"
        ),
        contact_sales=required_config.is_custom_priced,
    )


# ══════════════════════════════════════════════════════════════════════════
# The plan read
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class PlanContext:
    """A caller's resolved commercial state, read once per request."""

    plan: str
    tier: str
    display_name: str
    status: Optional[str] = None
    overrides: Mapping[str, Any] = field(default_factory=dict)

    def limit(self, resource: str) -> int:
        return SubscriptionEngine.get_effective_quotas(self.plan, self.overrides).get(resource, 0)


#: Cache TTL for the resolved plan. Short enough that an upgrade takes effect promptly even if
#: the explicit invalidation below is missed, long enough that a route carrying three gates makes
#: one profile read rather than three. ``invalidate_plan_cache`` is called by
#: ``billing._apply_billing_entitlement`` so a paid upgrade is visible immediately.
_PLAN_CACHE_TTL = 15

#: Subscription statuses that revoke paid entitlement.
#:
#: ``expired`` only. A ``cancelled`` subscription is still entitled until the period it has
#: already paid for ends — the same rule the marketplace applies in
#: ``design/subscriptionState.js`` and ``entitlement_resolver`` — and ``past_due`` /
#: ``payment_failed`` are left to the existing dunning behaviour rather than being turned into an
#: instant downgrade here. Changing that would be a billing-policy decision, not an entitlement
#: one, and this module is not where it belongs.
_REVOKING_STATUSES = frozenset({"expired"})


#: The ``profiles`` column sets this read will accept, widest first.
#:
#: WHY THERE IS MORE THAN ONE
#: --------------------------
#: ``plan_limit_overrides`` arrives with migration ``017_plan_entitlements.sql``, and code can
#: reach a database that has not run it yet — a deploy that lands before the migration, a replica
#: mid-rollout, a developer's own Supabase project. PostgREST answers a request for a column it
#: does not have with ``42703 undefined column``, which fails the WHOLE select: the tier and the
#: subscription status come back with it, not just the new column.
#:
#: That matters because this read fails CLOSED in production — correctly, since an unreadable
#: profile is not evidence of a Free account. Combined, the two behaviours would turn a missing
#: column into a 500 on every gated route until the migration ran, making the deploy order
#: load-bearing in the most expensive possible way.
#:
#: So the read degrades by COLUMN rather than by outcome: it asks for everything, and on failure
#: asks again for the set that predates the migration. An account on an un-migrated database keeps
#: its plan and its gates, and simply has no contractual overrides to apply — which is the truth,
#: because the column that would hold them does not exist. The retry logs a warning naming the
#: migration, so the skew is visible rather than silent.
_PROFILE_COLUMN_SETS = (
    "subscription_tier,subscription_status,plan_limit_overrides",
    "subscription_tier,subscription_status",
)


def _plan_cache_key(user_id: str) -> str:
    return f"entitlement:plan:{user_id}"


async def invalidate_plan_cache(user_id: str) -> None:
    """Drop a user's cached plan so a billing change takes effect on the next request."""
    try:
        await redis_manager.delete(_plan_cache_key(user_id))
    except Exception as exc:
        logger.debug("Plan cache invalidation skipped for %s: %s", user_id, exc)


async def get_plan_context(user_id: str, supabase: Any) -> PlanContext:
    """Resolve the caller's plan, subscription status and contractual overrides.

    Fails CLOSED in production: an unreadable profile raises rather than defaulting to Free.
    Defaulting to Free would look like a safe fallback and is not — it silently strips a paying
    customer of the capacity they bought the moment the database hiccups.
    """
    try:
        cached = await redis_manager.get(_plan_cache_key(user_id))
        if cached:
            payload = json.loads(cached)
            return PlanContext(
                plan=payload["plan"],
                tier=payload["tier"],
                display_name=payload["display_name"],
                status=payload.get("status"),
                overrides=payload.get("overrides") or {},
            )
    except Exception as exc:
        logger.debug("Plan cache read skipped for %s: %s", user_id, exc)

    if not supabase:
        if _is_production():
            raise RuntimeError(
                "CRITICAL: Supabase unavailable in production. Subscription verification failed "
                "to prevent unauthorized access."
            )
        return _free_context()

    response = None
    for columns in _PROFILE_COLUMN_SETS:
        try:
            query = supabase.table("profiles").select(columns).eq("id", user_id)
            result = query.execute()
            response = await result if inspect.isawaitable(result) else result
            break
        except Exception as exc:
            # A column this deployment's schema does not have yet. `plan_limit_overrides` arrives
            # with migration 017, and code can reach a database that has not run it — so the read
            # retries with the column set that predates the migration rather than failing closed on
            # a schema skew. See `_PROFILE_COLUMN_SETS`.
            if columns is not _PROFILE_COLUMN_SETS[-1]:
                logger.warning(
                    "profiles read with columns %r failed for %s (%s); retrying with the "
                    "pre-migration column set. Apply migration 017_plan_entitlements.sql.",
                    columns,
                    user_id,
                    exc,
                )
                continue
            logger.error("Failed to read plan for %s: %s", user_id, exc)
            if _is_production():
                raise RuntimeError(
                    f"CRITICAL: Subscription verification failed: {exc}. Operation blocked to "
                    "prevent unauthorized access in production."
                ) from exc
            return _free_context()

    rows = getattr(response, "data", None) or []
    if not rows:
        return _free_context()

    row = rows[0]
    stored = row.get("subscription_tier")
    subscription_status = row.get("subscription_status")
    overrides = row.get("plan_limit_overrides") or {}
    if not isinstance(overrides, Mapping):
        overrides = {}

    plan = SubscriptionEngine.migrate_plan_key(stored)
    if isinstance(subscription_status, str) and subscription_status.strip().lower() in _REVOKING_STATUSES:
        # An expired subscription is not a paid one. The stored tier is left untouched — this is
        # an entitlement decision for this request, not a write.
        logger.info(
            "Plan %s on user %s is not entitled: subscription_status=%s",
            plan,
            user_id,
            subscription_status,
        )
        plan = Plan.FREE.value

    config = SubscriptionEngine.get_plan_config(plan)
    context = PlanContext(
        plan=config.id,
        tier=config.tier,
        display_name=config.name,
        status=subscription_status,
        overrides=dict(overrides),
    )

    try:
        await redis_manager.set(
            _plan_cache_key(user_id),
            json.dumps(
                {
                    "plan": context.plan,
                    "tier": context.tier,
                    "display_name": context.display_name,
                    "status": context.status,
                    "overrides": dict(context.overrides),
                }
            ),
            ex=_PLAN_CACHE_TTL,
        )
    except Exception as exc:
        logger.debug("Plan cache write skipped for %s: %s", user_id, exc)

    return context


def _free_context() -> PlanContext:
    config = SubscriptionEngine.get_plan_config(Plan.FREE.value)
    return PlanContext(plan=config.id, tier=config.tier, display_name=config.name)


async def get_user_plan(user_id: str, supabase: Any) -> str:
    """The caller's canonical plan id. Preserved signature; now delegates to the context read."""
    context = await get_plan_context(user_id, supabase)
    return context.plan


_get_user_plan = get_user_plan


# ══════════════════════════════════════════════════════════════════════════
# The gates
# ══════════════════════════════════════════════════════════════════════════


async def require_feature(
    feature: str,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Assert the caller's plan includes ``feature``, or refuse with a structured 403."""
    context = await get_plan_context(user["id"], supabase)
    if not await SubscriptionEngine.check_feature_entitlement(user["id"], context.plan, feature):
        raise build_feature_refusal(feature, context.plan).as_http()
    return True


async def check_feature_optional(
    feature: str,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
) -> bool:
    """Whether the caller has ``feature``. Returns a boolean instead of raising."""
    context = await get_plan_context(user["id"], supabase)
    return await SubscriptionEngine.check_feature_entitlement(user["id"], context.plan, feature)


async def enforce_quota(
    resource: str,
    user: dict,
    supabase: Any,
    parent_id: Optional[str] = None,
    reserve: bool = False,
    idempotency_key: Optional[str] = None,
) -> int:
    """Assert the caller may add one more unit of ``resource``. Returns the measured usage.

    Counted resources are counted from the persistence layer. Metered resources are RESERVED —
    the increment happens inside the check, atomically, so two concurrent requests cannot both
    be told the last unit is free.
    """
    context = await get_plan_context(user["id"], supabase)
    limit = SubscriptionEngine.get_effective_quotas(context.plan, context.overrides).get(resource, 0)

    if SubscriptionEngine.is_unlimited(limit):
        return 0

    if resource in METERED_RESOURCES and reserve:
        allowed, current, effective = await usage_ledger.reserve(
            resource,
            user["id"],
            context.plan,
            supabase=supabase,
            limit=limit,
            idempotency_key=idempotency_key,
        )
        if not allowed:
            raise build_quota_refusal(resource, context.plan, current, effective).as_http()
        return current

    try:
        if resource in METERED_RESOURCES:
            current = await usage_ledger.usage(resource, user["id"], supabase)
        else:
            current = await usage_ledger.count(resource, user["id"], supabase, parent_id=parent_id)
    except UsageReadFailed as exc:
        # A figure that could not be read is not a figure of zero. 503, not a pass.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "ENTITLEMENT_USAGE_UNREADABLE", "message": str(exc)},
        ) from exc

    if current >= limit:
        raise build_quota_refusal(resource, context.plan, current, limit).as_http()
    return current


async def require_quota(
    resource: str,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Preserved public name. Delegates to :func:`enforce_quota`."""
    await enforce_quota(resource, user, supabase, reserve=resource in METERED_RESOURCES)
    return True


def feature_guard(feature: str, name: Optional[str] = None) -> Callable:
    """A FastAPI dependency asserting one feature. Used to declare the named gates below.

    ``name`` sets the function's ``__name__``, and it is not cosmetic. ``Depends(...)``'s repr is
    the callable's name, several route-contract tests assert on that repr
    (``tests/test_training_endpoints.py``, ``tests/test_sb06_exchange_agnostic_save.py``), and a
    stack trace is easier to follow when the name in it matches the name the route imported. So
    every gate below passes the public alias it is bound to rather than letting the factory derive
    a near-miss like ``require_ml_training`` → ``require_ml_training`` but
    ``check_strategy_quota`` → ``check_strategies_quota``.
    """

    async def _dependency(
        user: dict = Depends(get_current_user),
        supabase: Any = Depends(get_request_supabase),
    ):
        return await require_feature(feature, user, supabase)

    _dependency.__name__ = name or f"require_{feature}"
    return _dependency


def quota_guard(resource: str, reserve: bool = False, name: Optional[str] = None) -> Callable:
    """A FastAPI dependency asserting one capacity limit. See :func:`feature_guard` on ``name``."""

    async def _dependency(
        user: dict = Depends(get_current_user),
        supabase: Any = Depends(get_request_supabase),
    ):
        await enforce_quota(resource, user, supabase, reserve=reserve)
        return True

    _dependency.__name__ = name or f"check_{resource}_quota"
    return _dependency


# ── Feature gates ─────────────────────────────────────────────────────────

require_live_trading = feature_guard(Feature.LIVE_TRADING.value, name="require_live_trading")
require_ml_training = feature_guard(Feature.ML_TRAINING.value, name="require_ml_training")
require_optimization = feature_guard(Feature.OPTIMIZATION.value, name="require_optimization")
require_advanced_risk = feature_guard(Feature.ADVANCED_RISK.value, name="require_advanced_risk")
require_marketplace_browse = feature_guard(
    Feature.MARKETPLACE_BROWSE.value, name="require_marketplace_browse"
)
require_marketplace_subscribe = feature_guard(
    Feature.MARKETPLACE_SUBSCRIBE.value, name="require_marketplace_subscribe"
)
require_marketplace_publish = feature_guard(
    Feature.MARKETPLACE_PUBLISH.value, name="require_marketplace_publish"
)

#: DEPRECATED name, preserved so existing route declarations keep resolving. It now asserts
#: BROWSE, which every plan has — the transacting gates are ``require_marketplace_subscribe`` and
#: ``require_marketplace_publish``, and the routes that transact have been moved onto those. Left
#: in place because a name removed from here becomes an ImportError at app startup.
require_marketplace_access = require_marketplace_browse


# ── Capacity gates ────────────────────────────────────────────────────────

check_strategy_quota = quota_guard(Resource.STRATEGIES.value, name="check_strategy_quota")
check_paper_strategy_quota = quota_guard(
    Resource.PAPER_STRATEGIES.value, name="check_paper_strategy_quota"
)
check_bot_quota = quota_guard(Resource.BOTS.value, name="check_bot_quota")
check_exchange_connection_quota = quota_guard(
    Resource.EXCHANGE_CONNECTIONS.value, name="check_exchange_connection_quota"
)
check_trading_account_quota = quota_guard(
    Resource.TRADING_ACCOUNTS.value, name="check_trading_account_quota"
)
check_ml_model_quota = quota_guard(Resource.ML_MODELS.value, name="check_ml_model_quota")
check_marketplace_publish_quota = quota_guard(
    Resource.MARKETPLACE_PUBLISHED.value, name="check_marketplace_publish_quota"
)
check_marketplace_subscription_quota = quota_guard(
    Resource.MARKETPLACE_SUBSCRIPTIONS.value, name="check_marketplace_subscription_quota"
)

#: Metered gates RESERVE as they check. The reservation is the check.
check_backtest_quota = quota_guard(
    Resource.BACKTESTS.value, reserve=True, name="check_backtest_quota"
)
check_optimization_quota = quota_guard(
    Resource.OPTIMIZATIONS.value, reserve=True, name="check_optimization_quota"
)
#: Named ``check_ml_quota`` rather than ``check_ml_training_quota`` because that is the name the
#: routes import and the name several route-contract tests look for in ``Depends(...)``'s repr.
#: ``check_ml_training_quota`` is the alias, not the other way round.
check_ml_training_quota = quota_guard(
    Resource.ML_TRAININGS.value, reserve=True, name="check_ml_quota"
)

#: DEPRECATED name for the ML training meter, preserved for existing route declarations.
check_ml_quota = check_ml_training_quota


async def check_ml_model_slot(
    user: dict,
    supabase: Any,
    version_id: Optional[str] = None,
    node_id: Optional[str] = None,
) -> None:
    """Assert the caller may hold one more active ML model.

    NOT a bare dependency, because whether training consumes a model slot depends on WHICH node is
    being trained. ``model_versions`` carries ``uq_mv_active_per_node``, a partial unique index over
    ``(version_id, node_id) WHERE is_active`` — so at most one model is active per node, and
    retraining a node that already has one REPLACES it. The active-model count does not rise.

    A plain quota dependency could not see that difference and refused every retrain once an
    account reached its model limit, which is wrong in the same way refusing an exchange key
    ROTATION at the connection limit is wrong: the operation adds nothing to the thing being
    limited. The limit is on how many models you HOLD, and a replacement holds the same number.

    Refused only when the model would be a NEW one — a node with no active model, pushing the count
    past the plan's cap (Pro Quant 5, Business 15). ``node_id`` omitted means "every model node in
    the graph", which cannot be resolved to a single slot here; that case is admitted and bounded by
    the monthly training meter, which no path can bypass.
    """
    context = await get_plan_context(user["id"], supabase)
    limit = SubscriptionEngine.get_effective_quotas(context.plan, context.overrides).get(
        Resource.ML_MODELS.value, 0
    )
    if SubscriptionEngine.is_unlimited(limit):
        return

    # Zero allowance is a feature refusal, not a capacity one, and `require_ml_training` has
    # already made it. Reaching here with a zero limit means the plan grants ML but no models,
    # which no plan does; refuse rather than divide by it.
    if limit <= 0:
        raise build_quota_refusal(Resource.ML_MODELS.value, context.plan, 0, limit).as_http()

    try:
        held = await usage_ledger.count(Resource.ML_MODELS.value, user["id"], supabase)
    except UsageReadFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "ENTITLEMENT_USAGE_UNREADABLE", "message": str(exc)},
        ) from exc

    if held < limit:
        return

    # At the cap. Admitted only if this node already holds an active model, because completing the
    # run replaces that model rather than adding one.
    if version_id and node_id:
        try:
            replacing = await usage_ledger.has_active_model(
                user["id"], supabase, version_id, node_id
            )
        except UsageReadFailed as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error": "ENTITLEMENT_USAGE_UNREADABLE", "message": str(exc)},
            ) from exc
        if replacing:
            return

    raise build_quota_refusal(Resource.ML_MODELS.value, context.plan, held, limit).as_http()


async def check_exchange_connection_slot(
    user: dict,
    supabase: Any,
    exchange_id: str,
) -> None:
    """Assert the caller may connect ``exchange_id``.

    NOT a bare dependency, because whether this consumes a slot depends on WHICH venue is being
    connected. ``exchange_keys`` carries ``UNIQUE (user_id, exchange_id)``, so storing credentials
    for a venue the account is already connected to is a key ROTATION — it creates no new
    connection and must not be refused. A plain quota dependency could not see the difference and
    would lock an account at its limit out of rotating its own keys, which is a security-relevant
    operation and the last thing a plan limit should obstruct.
    """
    context = await get_plan_context(user["id"], supabase)
    limit = SubscriptionEngine.get_effective_quotas(context.plan, context.overrides).get(
        Resource.EXCHANGE_CONNECTIONS.value, 0
    )
    if SubscriptionEngine.is_unlimited(limit):
        return

    wanted = str(exchange_id or "").strip().lower()
    try:
        existing = await usage_ledger.exchange_ids(user["id"], supabase)
    except UsageReadFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "ENTITLEMENT_USAGE_UNREADABLE", "message": str(exc)},
        ) from exc

    if wanted in existing:
        # Rotation, not a new connection.
        return

    if len(existing) >= limit:
        raise build_quota_refusal(
            Resource.EXCHANGE_CONNECTIONS.value, context.plan, len(existing), limit
        ).as_http()


async def check_strategy_version_quota(
    strategy_id: str,
    user: dict,
    supabase: Any,
) -> int:
    """Assert one more version may be created for ``strategy_id``.

    Not a bare dependency because the limit is per strategy, so it needs the parent id. Callers
    invoke it from inside the handler once ownership of the strategy has been established —
    which must happen first, since this counts versions of a row it assumes the caller owns.
    """
    return await enforce_quota(
        Resource.STRATEGY_VERSIONS.value, user, supabase, parent_id=strategy_id
    )


# ── Usage writes ──────────────────────────────────────────────────────────


async def increment_usage(resource: str, user: dict = Depends(get_current_user)):
    """Record consumption of a metered resource."""
    new_usage = await SubscriptionEngine.increment_quota_usage(user["id"], resource)
    logger.info("Incremented %s usage for user %s: %s", resource, user["id"], new_usage)
    return new_usage


async def decrement_usage(resource: str, user: dict = Depends(get_current_user)):
    """Release a metered reservation whose work never started.

    Counted resources are a no-op here: they are counted from the persistence layer, so deleting
    the row IS the decrement and a counter write would only create something to drift.
    """
    if resource in COUNTED_RESOURCES:
        logger.debug(
            "decrement_usage(%s) is a no-op: the figure is counted from the persistence layer.",
            resource,
        )
        return 0
    new_usage = await SubscriptionEngine.decrement_quota_usage(user["id"], resource)
    logger.info("Decremented %s usage for user %s: %s", resource, user["id"], new_usage)
    return new_usage


async def get_user_entitlements(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """A caller's full entitlement state: plan, features, limits, measured usage, over-capacity."""
    context = await get_plan_context(user["id"], supabase)
    figures, unavailable = await usage_ledger.collect_usage(user["id"], supabase)
    entitlements = await SubscriptionEngine.get_user_entitlements(
        user["id"], context.plan, figures, overrides=context.overrides
    )
    # Carried alongside the figures so a client renders a reason where a number is missing
    # instead of an invented zero.
    entitlements.usage_unavailable = unavailable
    return entitlements
