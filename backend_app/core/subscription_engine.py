"""
core/subscription_engine.py — Centralized Subscription Capability Engine

THE ONE PLAN CATALOGUE
======================
This module is the single authoritative definition of the VyomQuant plan ladder: the
identifiers, the display names, the published prices, the per-plan capacity limits and the
feature set. Everything else in the platform — the entitlement dependencies, the billing
router, ``PricingService``, the marketplace gates, the ``/api/billing/plans`` and
``/api/billing/entitlements`` payloads the frontend renders — DERIVES from here. There is
deliberately no second copy of these numbers anywhere in the backend, and the frontend reads
them over the wire rather than restating them.

THE LADDER, AND WHY THE STORED IDENTIFIERS LOOK MISMATCHED
=========================================================
``profiles.subscription_tier`` is a live production column written by
``billing._apply_billing_entitlement`` with the raw checkout ``item_key``. Renaming a value in
that column would silently reassign paying customers, so the stored identifiers are kept
exactly as they are and the DISPLAY name is what moved:

    stored id       tier key        display name    published price
    ------------    ------------    ------------    ---------------
    free            FREE            Free            ₹0
    starter         TRADER          Trader          ₹499 / mo, ₹4,990 / yr
    pro             PRO_QUANT       Pro Quant       ₹999 / mo, ₹9,990 / yr
    enterprise      BUSINESS        Business        ₹2,499 / mo, ₹24,990 / yr
    scale           ENTERPRISE      Enterprise      custom

``enterprise`` is the id of the **Business** tier. That is not a typo. The historic
``enterprise`` plan was the ₹2,499 top tier, which is precisely the commercial position the new
Business tier occupies, so every existing ₹2,499 subscriber lands on Business with no migration
and no capacity loss (Business raises every one of that plan's limits). The genuinely new tier
is the custom-priced one, and it takes a NEW id, ``scale``.

The alternative — making ``enterprise`` mean the new custom tier and mapping the legacy value
forward — was rejected because ``migrate_plan_key`` resolves a live ``Plan`` value to itself
before consulting the alias table. A stored ``enterprise`` would then be ambiguous forever:
either a legacy ₹2,499 subscriber or a new custom account. Ambiguity on a billing column is not
an acceptable trade for a tidier name.

WHAT IS METERED AND WHAT IS COUNTED
===================================
Two different kinds of limit, enforced two different ways (see :mod:`core.usage_ledger`):

* **Counted** resources are point-in-time facts — how many strategies exist right now, how many
  exchange credentials are stored, how many models are active. These are counted from the
  persistence layer at check time. A counter would drift; the table cannot.
* **Metered** resources are monthly consumption — backtests, optimizations, ML training runs.
  These accumulate in a period-scoped ledger and are **never decremented**, which is what stops
  a user resetting their month by deleting the artefact they produced.

WHAT IS DELIBERATELY NOT METERED
================================
Historical data, tick data, standard market data, alerts, webhooks, basic analytics and
marketplace browsing carry no plan limit. Where a technical protection exists for them it stays
(rate limits, ``TenantQuota`` infrastructure ceilings); none of it is exposed as a commercial
gate. There are no team, workspace, RBAC or customer-API dimensions in this catalogue because
the product is single-user and has no customer API tier.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

from backend_app.core.cache import redis_manager

logger = logging.getLogger("SubscriptionEngine")


class Plan(Enum):
    """Subscription plan identifiers, as stored in ``profiles.subscription_tier``.

    These strings are persisted production values and are also the ``item_key`` a checkout
    carries through the gateway and back on the webhook. Adding a member is safe; renaming or
    removing one is not. See the module docstring for why ``ENTERPRISE`` is the Business tier.
    """

    FREE = "free"
    STARTER = "starter"          # displayed as "Trader"
    PRO = "pro"                  # displayed as "Pro Quant"
    ENTERPRISE = "enterprise"    # displayed as "Business" — the historic ₹2,499 tier
    SCALE = "scale"              # displayed as "Enterprise" — custom, sales-led


class PlanTier(Enum):
    """The ladder key used in code, tests, entitlement errors and upgrade copy.

    Separate from :class:`Plan` so that the vocabulary a reviewer, a test and an API error use
    (``PRO_QUANT``, ``BUSINESS``) is not tied to the historic stored spelling (``pro``,
    ``enterprise``). One catalogue, indexed two ways — never two catalogues.
    """

    FREE = "FREE"
    TRADER = "TRADER"
    PRO_QUANT = "PRO_QUANT"
    BUSINESS = "BUSINESS"
    ENTERPRISE = "ENTERPRISE"


class Feature(Enum):
    """Capability flags gated by plan.

    ``UNLIMITED_BACKTESTING`` / ``UNLIMITED_BUILDER`` are retained as DEPRECATED aliases only.
    Backtesting is metered now, so "unlimited" was a false claim; the honest flags are
    :attr:`BACKTESTING` and :attr:`STRATEGY_BUILDER`. ``_FEATURE_ALIASES`` keeps any existing
    caller that asks for the old spelling answering correctly.
    """

    # ── Core, every plan ────────────────────────────────────────────────
    STRATEGY_BUILDER = "strategy_builder"
    BACKTESTING = "backtesting"
    PAPER_TRADING = "paper_trading"
    MARKETPLACE_BROWSE = "marketplace_browse"
    BASIC_ANALYTICS = "basic_analytics"
    HISTORICAL_DATA = "historical_data"
    TICK_DATA = "tick_data"
    ALERTS = "alerts"

    # ── Trader and above ────────────────────────────────────────────────
    LIVE_TRADING = "live_trading"
    MULTI_EXCHANGE = "multi_exchange"
    ADVANCED_BACKTESTING = "advanced_backtesting"
    ADVANCED_RISK = "advanced_risk"
    EXECUTION_MONITORING = "execution_monitoring"
    ADVANCED_ANALYTICS = "advanced_analytics"
    OPTIMIZATION = "optimization"
    MARKETPLACE_SUBSCRIBE = "marketplace_subscribe"

    # ── Pro Quant and above ─────────────────────────────────────────────
    ADVANCED_BUILDER = "advanced_builder"
    ML_TRAINING = "ml_training"
    ML_NODES = "ml_nodes"
    PORTFOLIO_RISK = "portfolio_risk"
    STRATEGY_COMPARISON = "strategy_comparison"
    STRATEGY_VERSIONING = "strategy_versioning"
    MARKETPLACE_PUBLISH = "marketplace_publish"
    CREATOR_REVENUE = "creator_revenue"
    PRIORITY_SUPPORT = "priority_support"

    # ── Business and above ──────────────────────────────────────────────
    ADVANCED_PORTFOLIO_RISK = "advanced_portfolio_risk"
    OPERATIONAL_MONITORING = "operational_monitoring"
    AUDIT_HISTORY = "audit_history"
    DEDICATED_EXECUTION = "dedicated_execution"

    # ── DEPRECATED aliases. Resolved via _FEATURE_ALIASES, never listed on a plan. ──
    MARKETPLACE_ACCESS = "marketplace_access"          # → marketplace_browse
    UNLIMITED_BACKTESTING = "unlimited_backtesting"    # → backtesting
    UNLIMITED_BUILDER = "unlimited_builder"            # → strategy_builder
    API_ACCESS = "api_access"                          # not a pricing dimension; see below


#: Deprecated feature spelling → the flag that actually carries the capability.
#:
#: ``marketplace_access`` used to mean "may transact in the marketplace" and was withheld from
#: Free and Trader, which under the new rules would wrongly block browsing. It now resolves to
#: ``marketplace_browse`` (every plan), and the transacting capabilities have their own flags:
#: ``marketplace_subscribe`` and ``marketplace_publish``.
#:
#: ``api_access`` is NOT in this map. The product has no customer-facing API tier, so it is not
#: a pricing dimension and no plan grants it; a caller asking for it gets a truthful ``False``
#: rather than an alias to something else.
_FEATURE_ALIASES: Dict[str, str] = {
    Feature.MARKETPLACE_ACCESS.value: Feature.MARKETPLACE_BROWSE.value,
    Feature.UNLIMITED_BACKTESTING.value: Feature.BACKTESTING.value,
    Feature.UNLIMITED_BUILDER.value: Feature.STRATEGY_BUILDER.value,
}


class Resource(Enum):
    """Metered and counted resource keys.

    ``STRATEGIES``, ``BOTS``, ``ML_TRAININGS`` and ``MARKETPLACE_PUBLISHED`` keep their historic
    spellings because live Redis keys, the ``/api/billing/entitlements`` payload and the billing
    page's tiles are all keyed on them.
    """

    # Point-in-time counts (counted from the persistence layer)
    STRATEGIES = "strategies"                                 # active, non-archived strategies
    PAPER_STRATEGIES = "paper_strategies"                     # paper deployments
    BOTS = "bots"                                             # live deployments
    EXCHANGE_CONNECTIONS = "exchange_connections"
    TRADING_ACCOUNTS = "trading_accounts"
    CUSTOM_INDICATORS = "custom_indicators"
    STRATEGY_VERSIONS = "strategy_versions"                   # scoped PER STRATEGY
    ML_MODELS = "ml_models"                                   # active models
    MARKETPLACE_SUBSCRIPTIONS = "marketplace_subscriptions"
    MARKETPLACE_PUBLISHED = "marketplace_published"           # marketplace listings

    # Monthly meters (accumulate in the usage ledger, never decremented)
    BACKTESTS = "backtests"
    OPTIMIZATIONS = "optimizations"
    ML_TRAININGS = "ml_trainings"


#: Resources whose limit is consumption over a calendar month. Never decremented, so deleting
#: the artefact a run produced does not hand the month back.
METERED_RESOURCES: Tuple[str, ...] = (
    Resource.BACKTESTS.value,
    Resource.OPTIMIZATIONS.value,
    Resource.ML_TRAININGS.value,
)

#: Resources whose limit is a point-in-time count, counted from the persistence layer.
COUNTED_RESOURCES: Tuple[str, ...] = (
    Resource.STRATEGIES.value,
    Resource.PAPER_STRATEGIES.value,
    Resource.BOTS.value,
    Resource.EXCHANGE_CONNECTIONS.value,
    Resource.TRADING_ACCOUNTS.value,
    Resource.CUSTOM_INDICATORS.value,
    Resource.STRATEGY_VERSIONS.value,
    Resource.ML_MODELS.value,
    Resource.MARKETPLACE_SUBSCRIPTIONS.value,
    Resource.MARKETPLACE_PUBLISHED.value,
)

#: ``STRATEGY_VERSIONS`` is a per-strategy limit, not a per-account one: "3 versions per
#: strategy". Recorded here so a caller cannot accidentally treat it as an account total.
PER_PARENT_RESOURCES: Tuple[str, ...] = (Resource.STRATEGY_VERSIONS.value,)

#: The sentinel for "no fixed catalogue limit — set per contract". Used ONLY by the custom
#: ``scale`` tier, whose real numbers arrive from ``profiles.plan_limit_overrides``. It is not a
#: promise of unlimited capacity and is never rendered as "Unlimited": the UI renders "Custom".
CUSTOM_LIMIT = -1

#: The creator's share of a marketplace subscription payment, in whole percent. The authoritative
#: arithmetic lives in :func:`backend_app.backend.marketplace.money.split_ninety_ten`, which
#: computes the platform fee as the integer RESIDUAL so the two always sum to the amount. This
#: constant exists so the catalogue can state the share a plan earns; it is asserted equal to
#: ``money.OWNER_SHARE_PERCENT`` by the test suite rather than duplicated as a second source.
CREATOR_REVENUE_SHARE_PERCENT = 90


@dataclass(frozen=True)
class PlanPrice:
    """A published price for one billing interval, in minor units per currency.

    ``{"USD": 500, "INR": 49900}`` is $5.00 and ₹499.00. Both are PUBLISHED prices, not
    conversions of one another: the INR figure is the committed rupee price list and is charged
    verbatim rather than FX-derived. ``PricingService`` converts only for currencies that have
    no published figure here.
    """

    minor: Mapping[str, int]

    def get(self, currency: str, default: int = 0) -> int:
        return int(self.minor.get(currency.upper(), default))

    def has(self, currency: str) -> bool:
        return currency.upper() in self.minor


@dataclass
class PlanConfig:
    """One plan, whole.

    ``quotas`` / ``features`` / ``pricing`` keep their original names and types so every existing
    reader (``PricingService.get_localized_plans``, the entitlements endpoint, the billing page's
    tiles) keeps working unchanged. Everything else is additive.
    """

    id: str
    name: str
    description: str
    features: List[str]
    quotas: Dict[str, int]
    pricing: Dict[str, int]                     # monthly, minor units. Unchanged shape.

    # ── Additive: the ladder, the copy and the commercial terms ──────────
    tier: str = PlanTier.FREE.value
    journey: str = ""                           # "Explore" / "Automate" / "Quantify" / …
    tagline: str = ""
    badge: Optional[str] = None                 # e.g. "MOST POPULAR"
    cta_label: str = ""
    cta_secondary: Optional[str] = None
    pricing_annual: Dict[str, int] = field(default_factory=dict)
    is_custom_priced: bool = False
    creator_revenue_share_percent: int = 0
    rank: int = 0                               # ladder order, for upgrade/downgrade comparison
    upgrade_to: Optional[str] = None            # next plan id, or None at the top

    # Kept for symmetry with the published price list: the currencies whose figures are
    # committed rather than converted.
    @property
    def published_currencies(self) -> Tuple[str, ...]:
        return tuple(sorted(self.pricing.keys()))


@dataclass
class UserEntitlements:
    """A user's resolved entitlement state. Original four fields preserved; four added.

    ``usage_unavailable`` maps a resource to the REASON its figure is missing. It exists because
    the billing page renders a usage bar from ``usage``, and an absent figure reported as ``0``
    is a claim the backend cannot support — ``0 / 10`` and "this could not be read" look identical
    to a trader and mean opposite things.
    """

    plan: str
    features: List[str]
    quotas: Dict[str, int]
    usage: Dict[str, int]
    tier: str = PlanTier.FREE.value
    display_name: str = ""
    over_capacity: Dict[str, Dict[str, int]] = field(default_factory=dict)
    usage_unavailable: Dict[str, str] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════
# The usage period
# ══════════════════════════════════════════════════════════════════════════

#: The timezone whose calendar month defines a usage period. UTC by default so the boundary is
#: deterministic and does not move with a server's locale. An operator may set
#: ``PLAN_USAGE_TIMEZONE`` (e.g. ``Asia/Kolkata``) to align the reset with a local billing day.
_USAGE_TZ_NAME = os.getenv("PLAN_USAGE_TIMEZONE", "UTC")


def _usage_tzinfo():
    if _USAGE_TZ_NAME.upper() == "UTC":
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(_USAGE_TZ_NAME)
    except Exception as exc:  # pragma: no cover - configuration error path
        logger.error(
            "PLAN_USAGE_TIMEZONE=%r could not be resolved (%s); falling back to UTC so the "
            "usage period boundary stays deterministic.",
            _USAGE_TZ_NAME,
            exc,
        )
        return timezone.utc


def usage_period(moment: Optional[datetime] = None) -> str:
    """The ``YYYY-MM`` usage period a moment belongs to.

    One string, computed in one place, so every writer and every reader of the ledger agrees on
    where the month boundary is. A naive ``datetime`` is interpreted as UTC rather than as local
    time, because a naive local timestamp is the classic way two callers end up disagreeing
    about which month a run belongs to.
    """
    now = moment or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(_usage_tzinfo())
    return f"{local.year:04d}-{local.month:02d}"


class SubscriptionEngine:
    """Centralized subscription capability engine."""

    # ══════════════════════════════════════════════════════════════════════
    # THE CATALOGUE
    # ══════════════════════════════════════════════════════════════════════
    _PLANS: Dict[str, PlanConfig] = {
        Plan.FREE.value: PlanConfig(
            id=Plan.FREE.value,
            name="Free",
            description=(
                "Build your first strategy, backtest your ideas and experience the VyomQuant "
                "workflow without paying."
            ),
            tier=PlanTier.FREE.value,
            journey="Explore",
            tagline="Build before you commit.",
            cta_label="Start Free",
            rank=0,
            upgrade_to=Plan.STARTER.value,
            features=[
                Feature.STRATEGY_BUILDER.value,
                Feature.BACKTESTING.value,
                Feature.PAPER_TRADING.value,
                Feature.BASIC_ANALYTICS.value,
                Feature.HISTORICAL_DATA.value,
                Feature.TICK_DATA.value,
                Feature.ALERTS.value,
                Feature.MARKETPLACE_BROWSE.value,
            ],
            quotas={
                Resource.STRATEGIES.value: 1,
                Resource.PAPER_STRATEGIES.value: 1,
                Resource.BOTS.value: 0,
                Resource.EXCHANGE_CONNECTIONS.value: 1,
                Resource.TRADING_ACCOUNTS.value: 1,
                Resource.BACKTESTS.value: 10,
                Resource.CUSTOM_INDICATORS.value: 3,
                Resource.STRATEGY_VERSIONS.value: 3,
                Resource.ML_MODELS.value: 0,
                Resource.ML_TRAININGS.value: 0,
                Resource.OPTIMIZATIONS.value: 0,
                Resource.MARKETPLACE_SUBSCRIPTIONS.value: 0,
                Resource.MARKETPLACE_PUBLISHED.value: 0,
            },
            pricing={"USD": 0, "INR": 0},
            pricing_annual={"USD": 0, "INR": 0},
        ),
        Plan.STARTER.value: PlanConfig(
            id=Plan.STARTER.value,
            name="Trader",
            description=(
                "For traders who have moved beyond experimentation and want to automate their "
                "own strategies."
            ),
            tier=PlanTier.TRADER.value,
            journey="Automate",
            tagline="Turn your strategies into automated systems.",
            cta_label="Start Automating",
            rank=1,
            upgrade_to=Plan.PRO.value,
            features=[
                Feature.STRATEGY_BUILDER.value,
                Feature.BACKTESTING.value,
                Feature.PAPER_TRADING.value,
                Feature.BASIC_ANALYTICS.value,
                Feature.HISTORICAL_DATA.value,
                Feature.TICK_DATA.value,
                Feature.ALERTS.value,
                Feature.MARKETPLACE_BROWSE.value,
                Feature.LIVE_TRADING.value,
                Feature.MULTI_EXCHANGE.value,
                Feature.ADVANCED_BACKTESTING.value,
                Feature.ADVANCED_RISK.value,
                Feature.EXECUTION_MONITORING.value,
                Feature.ADVANCED_ANALYTICS.value,
                Feature.OPTIMIZATION.value,
                Feature.MARKETPLACE_SUBSCRIBE.value,
            ],
            quotas={
                Resource.STRATEGIES.value: 3,
                Resource.PAPER_STRATEGIES.value: 3,
                Resource.BOTS.value: 3,
                Resource.EXCHANGE_CONNECTIONS.value: 2,
                Resource.TRADING_ACCOUNTS.value: 1,
                Resource.BACKTESTS.value: 100,
                Resource.CUSTOM_INDICATORS.value: 10,
                Resource.STRATEGY_VERSIONS.value: 10,
                Resource.ML_MODELS.value: 0,
                Resource.ML_TRAININGS.value: 0,
                Resource.OPTIMIZATIONS.value: 25,
                Resource.MARKETPLACE_SUBSCRIPTIONS.value: 3,
                Resource.MARKETPLACE_PUBLISHED.value: 0,
            },
            # ₹499 / month, ₹4,990 / year — both PUBLISHED figures, charged verbatim.
            #
            # The USD column is UNCHANGED from the previous catalogue ($5.00). It is regional
            # pricing, not a conversion of the rupee figure, and altering it would change what
            # existing Stripe subscribers are billed — which the pricing brief did not ask for.
            # The annual USD figure follows the rupee ratio (ten months for twelve).
            pricing={"USD": 500, "INR": 49900},
            pricing_annual={"USD": 5000, "INR": 499000},
        ),
        Plan.PRO.value: PlanConfig(
            id=Plan.PRO.value,
            name="Pro Quant",
            description=(
                "For serious traders who want advanced quantitative capabilities and access to "
                "the VyomQuant Strategy Creator economy."
            ),
            tier=PlanTier.PRO_QUANT.value,
            journey="Quantify",
            tagline="Build. Optimize. Publish. Earn.",
            badge="MOST POPULAR",
            cta_label="Start Pro Quant",
            cta_secondary="Build it. Publish it. Earn from it.",
            rank=2,
            upgrade_to=Plan.ENTERPRISE.value,
            creator_revenue_share_percent=CREATOR_REVENUE_SHARE_PERCENT,
            features=[
                Feature.STRATEGY_BUILDER.value,
                Feature.BACKTESTING.value,
                Feature.PAPER_TRADING.value,
                Feature.BASIC_ANALYTICS.value,
                Feature.HISTORICAL_DATA.value,
                Feature.TICK_DATA.value,
                Feature.ALERTS.value,
                Feature.MARKETPLACE_BROWSE.value,
                Feature.LIVE_TRADING.value,
                Feature.MULTI_EXCHANGE.value,
                Feature.ADVANCED_BACKTESTING.value,
                Feature.ADVANCED_RISK.value,
                Feature.EXECUTION_MONITORING.value,
                Feature.ADVANCED_ANALYTICS.value,
                Feature.OPTIMIZATION.value,
                Feature.MARKETPLACE_SUBSCRIBE.value,
                Feature.ADVANCED_BUILDER.value,
                Feature.ML_TRAINING.value,
                Feature.ML_NODES.value,
                Feature.PORTFOLIO_RISK.value,
                Feature.STRATEGY_COMPARISON.value,
                Feature.STRATEGY_VERSIONING.value,
                Feature.MARKETPLACE_PUBLISH.value,
                Feature.CREATOR_REVENUE.value,
                Feature.PRIORITY_SUPPORT.value,
            ],
            quotas={
                Resource.STRATEGIES.value: 10,
                Resource.PAPER_STRATEGIES.value: 10,
                Resource.BOTS.value: 10,
                Resource.EXCHANGE_CONNECTIONS.value: 5,
                Resource.TRADING_ACCOUNTS.value: 3,
                Resource.BACKTESTS.value: 500,
                Resource.CUSTOM_INDICATORS.value: 30,
                Resource.STRATEGY_VERSIONS.value: 25,
                Resource.ML_MODELS.value: 5,
                Resource.ML_TRAININGS.value: 50,
                Resource.OPTIMIZATIONS.value: 100,
                Resource.MARKETPLACE_SUBSCRIPTIONS.value: 10,
                Resource.MARKETPLACE_PUBLISHED.value: 5,
            },
            # ₹999 / month, ₹9,990 / year. USD unchanged at $10.00; see the Trader note.
            pricing={"USD": 1000, "INR": 99900},
            pricing_annual={"USD": 10000, "INR": 999000},
        ),
        Plan.ENTERPRISE.value: PlanConfig(
            # NOTE: id "enterprise" is the BUSINESS tier. See the module docstring.
            id=Plan.ENTERPRISE.value,
            name="Business",
            description=(
                "For advanced traders and professional operators who need substantially more "
                "strategy, account and quantitative capacity."
            ),
            tier=PlanTier.BUSINESS.value,
            journey="Operate",
            tagline="Run a larger systematic trading operation.",
            cta_label="Scale to Business",
            cta_secondary="More strategies. More capacity. More opportunity.",
            rank=3,
            upgrade_to=Plan.SCALE.value,
            creator_revenue_share_percent=CREATOR_REVENUE_SHARE_PERCENT,
            features=[
                Feature.STRATEGY_BUILDER.value,
                Feature.BACKTESTING.value,
                Feature.PAPER_TRADING.value,
                Feature.BASIC_ANALYTICS.value,
                Feature.HISTORICAL_DATA.value,
                Feature.TICK_DATA.value,
                Feature.ALERTS.value,
                Feature.MARKETPLACE_BROWSE.value,
                Feature.LIVE_TRADING.value,
                Feature.MULTI_EXCHANGE.value,
                Feature.ADVANCED_BACKTESTING.value,
                Feature.ADVANCED_RISK.value,
                Feature.EXECUTION_MONITORING.value,
                Feature.ADVANCED_ANALYTICS.value,
                Feature.OPTIMIZATION.value,
                Feature.MARKETPLACE_SUBSCRIBE.value,
                Feature.ADVANCED_BUILDER.value,
                Feature.ML_TRAINING.value,
                Feature.ML_NODES.value,
                Feature.PORTFOLIO_RISK.value,
                Feature.STRATEGY_COMPARISON.value,
                Feature.STRATEGY_VERSIONING.value,
                Feature.MARKETPLACE_PUBLISH.value,
                Feature.CREATOR_REVENUE.value,
                Feature.PRIORITY_SUPPORT.value,
                Feature.ADVANCED_PORTFOLIO_RISK.value,
                Feature.OPERATIONAL_MONITORING.value,
                Feature.AUDIT_HISTORY.value,
                Feature.DEDICATED_EXECUTION.value,
            ],
            quotas={
                Resource.STRATEGIES.value: 25,
                Resource.PAPER_STRATEGIES.value: 25,
                Resource.BOTS.value: 25,
                Resource.EXCHANGE_CONNECTIONS.value: 8,
                Resource.TRADING_ACCOUNTS.value: 5,
                Resource.BACKTESTS.value: 1500,
                Resource.CUSTOM_INDICATORS.value: 75,
                Resource.STRATEGY_VERSIONS.value: 50,
                Resource.ML_MODELS.value: 15,
                Resource.ML_TRAININGS.value: 200,
                Resource.OPTIMIZATIONS.value: 400,
                Resource.MARKETPLACE_SUBSCRIPTIONS.value: 25,
                Resource.MARKETPLACE_PUBLISHED.value: 15,
            },
            # ₹2,499 / month, ₹24,990 / year. USD unchanged at $25.00; see the Trader note.
            pricing={"USD": 2500, "INR": 249900},
            pricing_annual={"USD": 25000, "INR": 2499000},
        ),
        Plan.SCALE.value: PlanConfig(
            id=Plan.SCALE.value,
            name="Enterprise",
            description=(
                "Custom capacity, deployment and support arranged around an existing trading "
                "operation."
            ),
            tier=PlanTier.ENTERPRISE.value,
            journey="Scale",
            tagline="Infrastructure built around your operation.",
            cta_label="Talk to Sales",
            rank=4,
            upgrade_to=None,
            is_custom_priced=True,
            creator_revenue_share_percent=CREATOR_REVENUE_SHARE_PERCENT,
            features=[
                # Every Business capability. Nothing beyond it is claimed here: an Enterprise
                # agreement raises CAPACITY and adds deployment/support terms, and this list is
                # rendered on a public pricing page, so it may not advertise a capability that
                # is not implemented.
                Feature.STRATEGY_BUILDER.value,
                Feature.BACKTESTING.value,
                Feature.PAPER_TRADING.value,
                Feature.BASIC_ANALYTICS.value,
                Feature.HISTORICAL_DATA.value,
                Feature.TICK_DATA.value,
                Feature.ALERTS.value,
                Feature.MARKETPLACE_BROWSE.value,
                Feature.LIVE_TRADING.value,
                Feature.MULTI_EXCHANGE.value,
                Feature.ADVANCED_BACKTESTING.value,
                Feature.ADVANCED_RISK.value,
                Feature.EXECUTION_MONITORING.value,
                Feature.ADVANCED_ANALYTICS.value,
                Feature.OPTIMIZATION.value,
                Feature.MARKETPLACE_SUBSCRIBE.value,
                Feature.ADVANCED_BUILDER.value,
                Feature.ML_TRAINING.value,
                Feature.ML_NODES.value,
                Feature.PORTFOLIO_RISK.value,
                Feature.STRATEGY_COMPARISON.value,
                Feature.STRATEGY_VERSIONING.value,
                Feature.MARKETPLACE_PUBLISH.value,
                Feature.CREATOR_REVENUE.value,
                Feature.PRIORITY_SUPPORT.value,
                Feature.ADVANCED_PORTFOLIO_RISK.value,
                Feature.OPERATIONAL_MONITORING.value,
                Feature.AUDIT_HISTORY.value,
                Feature.DEDICATED_EXECUTION.value,
            ],
            # CUSTOM_LIMIT means "set per contract", resolved from
            # profiles.plan_limit_overrides by get_effective_quotas(). It is NOT unlimited: an
            # Enterprise account with no override recorded falls back to the Business figure
            # (see _SCALE_FALLBACK_PLAN) rather than to infinity, so an unprovisioned contract
            # cannot become an uncapped one.
            quotas={resource.value: CUSTOM_LIMIT for resource in Resource},
            pricing={},            # no published price — quoted, not listed
            pricing_annual={},
        ),
    }

    #: Where a ``scale`` quota falls back when no per-account override is recorded. Business is
    #: the floor an Enterprise agreement starts from, so an unprovisioned account behaves as a
    #: Business account rather than as an uncapped one.
    _SCALE_FALLBACK_PLAN = Plan.ENTERPRISE.value

    # ── Legacy plan key mapping (old -> canonical) ────────────────────────
    #
    # Every historic value that may sit in profiles.subscription_tier or arrive as a checkout
    # item_key. `enterprise` is ABSENT on purpose: it is a live Plan value and
    # migrate_plan_key resolves those to themselves, which is exactly the behaviour the
    # Business tier needs.
    _PLAN_MIGRATION: Dict[str, str] = {
        "free": Plan.FREE.value,
        "sandbox": Plan.FREE.value,
        "starter": Plan.STARTER.value,
        "starter_499": Plan.STARTER.value,
        "trader": Plan.STARTER.value,
        "basic": Plan.STARTER.value,
        "BASIC": Plan.STARTER.value,
        "pro": Plan.PRO.value,
        "pro_999": Plan.PRO.value,
        "pro_quant": Plan.PRO.value,
        "professional": Plan.PRO.value,
        "PROFESSIONAL": Plan.PRO.value,
        "ml_addon": Plan.PRO.value,
        # The ₹2,499 historic top tier, under all of its past spellings, is the Business tier.
        "elite": Plan.ENTERPRISE.value,
        "elite_1999": Plan.ENTERPRISE.value,
        "business": Plan.ENTERPRISE.value,
        "institutional": Plan.ENTERPRISE.value,
        "INSTITUTIONAL": Plan.ENTERPRISE.value,
        "ENTERPRISE": Plan.ENTERPRISE.value,
        "enterprise": Plan.ENTERPRISE.value,
        # The new custom tier.
        "scale": Plan.SCALE.value,
        "enterprise_custom": Plan.SCALE.value,
    }

    # ══════════════════════════════════════════════════════════════════════
    # Identity and lookup
    # ══════════════════════════════════════════════════════════════════════

    @classmethod
    def migrate_plan_key(cls, old_key: str) -> str:
        """Normalise any historic or current plan spelling to a canonical :class:`Plan` value."""
        if not old_key:
            return Plan.FREE.value
        k = str(old_key).strip().lower()
        if k in [p.value for p in Plan]:
            return k
        return cls._PLAN_MIGRATION.get(old_key, cls._PLAN_MIGRATION.get(k, Plan.FREE.value))

    @classmethod
    def get_plan_config(cls, plan_key: str) -> Optional[PlanConfig]:
        """The plan's configuration, after normalising the key."""
        plan_key = cls.migrate_plan_key(plan_key)
        return cls._PLANS.get(plan_key)

    @classmethod
    def get_all_plans(cls) -> List[PlanConfig]:
        """Every plan, in ladder order."""
        return sorted(cls._PLANS.values(), key=lambda p: p.rank)

    @classmethod
    def plan_id_for_tier(cls, tier: str) -> Optional[str]:
        """``"PRO_QUANT"`` → ``"pro"``. The one place the two vocabularies meet."""
        wanted = str(tier or "").strip().upper()
        for config in cls._PLANS.values():
            if config.tier == wanted:
                return config.id
        return None

    @classmethod
    def tier_for_plan_id(cls, plan_key: str) -> str:
        """``"pro"`` → ``"PRO_QUANT"``. Falls back to ``FREE`` for an unknown id."""
        config = cls.get_plan_config(plan_key)
        return config.tier if config else PlanTier.FREE.value

    @classmethod
    def display_name(cls, plan_key: str) -> str:
        """The name a trader sees. ``"enterprise"`` → ``"Business"``."""
        config = cls.get_plan_config(plan_key)
        return config.name if config else "Free"

    @classmethod
    def plan_rank(cls, plan_key: str) -> int:
        config = cls.get_plan_config(plan_key)
        return config.rank if config else 0

    @classmethod
    def is_at_least(cls, plan_key: str, minimum_plan_key: str) -> bool:
        """Ladder comparison. ``is_at_least("pro", "starter")`` is ``True``."""
        return cls.plan_rank(plan_key) >= cls.plan_rank(minimum_plan_key)

    @classmethod
    def minimum_plan_for_feature(cls, feature: str) -> Optional[str]:
        """The cheapest plan id that grants ``feature``, or ``None`` if no plan does."""
        resolved = _FEATURE_ALIASES.get(feature, feature)
        for config in cls.get_all_plans():
            if resolved in config.features:
                return config.id
        return None

    @classmethod
    def minimum_plan_for_quota(cls, resource: str, required: int = 1) -> Optional[str]:
        """The cheapest plan id whose ``resource`` limit admits ``required`` units."""
        for config in cls.get_all_plans():
            limit = config.quotas.get(resource, 0)
            if limit == CUSTOM_LIMIT or limit >= required:
                return config.id
        return None

    @classmethod
    def next_plan(cls, plan_key: str) -> Optional[str]:
        """The next plan up the ladder, or ``None`` at the top."""
        config = cls.get_plan_config(plan_key)
        return config.upgrade_to if config else Plan.STARTER.value

    # ══════════════════════════════════════════════════════════════════════
    # Features
    # ══════════════════════════════════════════════════════════════════════

    @classmethod
    def has_feature(cls, plan_key: str, feature: str) -> bool:
        """Whether a plan grants a capability. Resolves deprecated feature spellings."""
        config = cls.get_plan_config(plan_key)
        if not config:
            return False
        resolved = _FEATURE_ALIASES.get(feature, feature)
        return resolved in config.features

    @classmethod
    async def check_feature_entitlement(cls, user_id: str, plan_key: str, feature: str) -> bool:
        """Whether ``user_id`` on ``plan_key`` may use ``feature``."""
        config = cls.get_plan_config(plan_key)
        if not config:
            logger.warning("Unknown plan key: %s for user %s", plan_key, user_id)
            return False

        resolved = _FEATURE_ALIASES.get(feature, feature)
        if resolved not in config.features:
            logger.info(
                "Feature %s not available on plan %s for user %s", resolved, plan_key, user_id
            )
            return False
        return True

    # ══════════════════════════════════════════════════════════════════════
    # Quotas
    # ══════════════════════════════════════════════════════════════════════

    @classmethod
    def get_quota_limit(cls, plan_key: str, resource: str) -> int:
        """The catalogue limit for ``resource`` on ``plan_key``, before any account override."""
        config = cls.get_plan_config(plan_key)
        if not config:
            return 0
        limit = config.quotas.get(resource, 0)
        if limit == CUSTOM_LIMIT and config.id == Plan.SCALE.value:
            fallback = cls._PLANS[cls._SCALE_FALLBACK_PLAN]
            return fallback.quotas.get(resource, 0)
        return limit

    @classmethod
    def get_effective_quotas(
        cls,
        plan_key: str,
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, int]:
        """Catalogue quotas with any per-account contractual override applied.

        Overrides exist for the custom ``scale`` tier, whose capacity is negotiated rather than
        listed. They are read from ``profiles.plan_limit_overrides`` and are **only honoured
        upward**: an override may raise a limit for a contracted account, never lower it below
        the plan it is paying for. A lower value would be a silent, invisible downgrade of a
        paying customer, which is not something a JSONB column should be able to do.
        """
        config = cls.get_plan_config(plan_key)
        if not config:
            config = cls._PLANS[Plan.FREE.value]

        resolved: Dict[str, int] = {}
        for resource in Resource:
            resolved[resource.value] = cls.get_quota_limit(config.id, resource.value)

        if not overrides or not isinstance(overrides, Mapping):
            return resolved

        for key, raw in overrides.items():
            if key not in resolved:
                continue
            try:
                candidate = int(raw)
            except (TypeError, ValueError):
                logger.warning(
                    "Ignoring non-integer plan_limit_override %s=%r on plan %s",
                    key,
                    raw,
                    config.id,
                )
                continue
            if candidate > resolved[key]:
                resolved[key] = candidate
            elif candidate < resolved[key]:
                logger.warning(
                    "Ignoring plan_limit_override %s=%d on plan %s: it is BELOW the plan's own "
                    "limit of %d and an override may not downgrade a paying account.",
                    key,
                    candidate,
                    config.id,
                    resolved[key],
                )
        return resolved

    @classmethod
    def is_unlimited(cls, limit: Any) -> bool:
        """Whether a limit value means 'no ceiling enforced here'.

        Only the custom tier's :data:`CUSTOM_LIMIT` and a literal infinity qualify. Kept as one
        predicate because the codebase historically spelt unlimited two ways (``-1`` in the
        catalogue, ``float("inf")`` in the retired ``DEPLOYMENT_LIMITS``).
        """
        return limit == CUSTOM_LIMIT or limit == float("inf")

    @classmethod
    async def check_quota_entitlement(
        cls,
        user_id: str,
        plan_key: str,
        resource: str,
        current_usage: int,
        limit: Optional[int] = None,
    ) -> bool:
        """Whether one more unit of ``resource`` is within the plan's limit.

        ``limit`` may be supplied by a caller that has already resolved account overrides; when
        omitted the catalogue limit is used.
        """
        config = cls.get_plan_config(plan_key)
        if not config:
            logger.warning("Unknown plan key: %s for user %s", plan_key, user_id)
            return False

        effective = cls.get_quota_limit(config.id, resource) if limit is None else limit

        if cls.is_unlimited(effective):
            return True

        if current_usage >= effective:
            logger.info(
                "Quota exhausted for %s: %s/%s user %s plan %s",
                resource,
                current_usage,
                effective,
                user_id,
                plan_key,
            )
            return False
        return True

    # ── Redis-backed monthly meters ──────────────────────────────────────
    #
    # These are the METERED resources only. A counted resource is counted from the persistence
    # layer by core.usage_ledger and never passes through here, because a counter that can drift
    # from the table it describes is worse than no counter.

    @staticmethod
    def _meter_key(user_id: str, resource: str, period: Optional[str] = None) -> str:
        """``quota:{user}:{resource}:{YYYY-MM}`` — the period-scoped meter key.

        The period is IN the key, which is what makes the month boundary work: a new month is a
        new key holding zero, with no scheduled job required to reset anything. The previous
        implementation used an unscoped key with a 24-hour TTL, so a "monthly" allowance silently
        reset every day.
        """
        return f"quota:{user_id}:{resource}:{period or usage_period()}"

    #: 40 days: comfortably longer than any month, short enough that abandoned periods expire.
    _METER_TTL_SECONDS = 60 * 60 * 24 * 40

    @classmethod
    async def reserve_quota(
        cls,
        user_id: str,
        plan_key: str,
        resource: str,
        amount: int = 1,
        limit: Optional[int] = None,
        period: Optional[str] = None,
    ) -> Tuple[bool, int, int]:
        """Atomically reserve monthly meter capacity.

        Returns ``(allowed, usage_after_the_decision, limit)``. The increment happens FIRST and
        is reverted on overflow, so two concurrent requests cannot both observe the last unit as
        free — the classic check-then-increment race. The reverted value is reported rather than
        the inflated one, so a refusal never tells a user they have consumed more than they have.
        """
        config = cls.get_plan_config(plan_key)
        if not config:
            return False, 0, 0

        effective = cls.get_quota_limit(config.id, resource) if limit is None else limit
        cache_key = cls._meter_key(user_id, resource, period)

        if cls.is_unlimited(effective):
            new_usage = await redis_manager.incrby(cache_key, amount)
            await redis_manager.expire(cache_key, cls._METER_TTL_SECONDS)
            return True, new_usage, effective

        if effective <= 0:
            # Nothing to reserve, and nothing is written: a plan with a zero allowance must not
            # accumulate a usage figure it can never spend.
            current = await cls.get_quota_usage(user_id, resource, period)
            return False, current, effective

        new_usage = await redis_manager.incrby(cache_key, amount)
        if new_usage == amount:
            await redis_manager.expire(cache_key, cls._METER_TTL_SECONDS)

        if new_usage > effective:
            reverted = await redis_manager.decrby(cache_key, amount)
            logger.info(
                "Monthly meter exhausted for %s: %s/%s user %s plan %s",
                resource,
                reverted,
                effective,
                user_id,
                plan_key,
            )
            return False, reverted, effective

        return True, new_usage, effective

    @classmethod
    async def increment_quota_usage(
        cls,
        user_id: str,
        resource: str,
        amount: int = 1,
        period: Optional[str] = None,
    ) -> int:
        """Increment a monthly meter."""
        cache_key = cls._meter_key(user_id, resource, period)
        new_usage = await redis_manager.incrby(cache_key, amount)
        await redis_manager.expire(cache_key, cls._METER_TTL_SECONDS)
        return new_usage

    @classmethod
    async def decrement_quota_usage(
        cls,
        user_id: str,
        resource: str,
        amount: int = 1,
        period: Optional[str] = None,
    ) -> int:
        """Release a monthly meter reservation.

        Only for releasing a reservation whose work never started (a job rejected downstream
        before execution). A COMPLETED run is never given back — neither a successful one nor a
        failed one — because "delete the artefact to reclaim the month" is exactly the bypass the
        meter exists to prevent.
        """
        cache_key = cls._meter_key(user_id, resource, period)
        new_usage = await redis_manager.decrby(cache_key, amount)
        if new_usage < 0:
            await redis_manager.set(cache_key, "0")
            new_usage = 0
        await redis_manager.expire(cache_key, cls._METER_TTL_SECONDS)
        return new_usage

    @classmethod
    async def get_quota_usage(
        cls,
        user_id: str,
        resource: str,
        period: Optional[str] = None,
    ) -> int:
        """The current monthly meter reading for ``resource``."""
        cache_key = cls._meter_key(user_id, resource, period)
        current = await redis_manager.get(cache_key)
        if current is None:
            return 0
        try:
            return max(0, int(current))
        except (ValueError, TypeError):
            return 0

    @classmethod
    async def get_user_entitlements(
        cls,
        user_id: str,
        plan_key: str,
        usage: Dict[str, int],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> "UserEntitlements":
        """Assemble a user's entitlement state from their plan and their measured usage."""
        config = cls.get_plan_config(plan_key)
        if not config:
            logger.warning(
                "get_user_entitlements: unknown plan_key=%r for user %s, falling back to FREE",
                plan_key,
                user_id,
            )
            config = cls.get_plan_config(Plan.FREE.value)

        quotas = cls.get_effective_quotas(config.id, overrides)

        # Over-capacity is a real, reportable state after a downgrade: the data is preserved and
        # the account simply cannot add more. Computed here so one shape serves the API, the
        # billing page's warning and the downgrade tests.
        over_capacity: Dict[str, Dict[str, int]] = {}
        for resource, limit in quotas.items():
            if cls.is_unlimited(limit):
                continue
            used = usage.get(resource)
            if isinstance(used, int) and used > limit:
                over_capacity[resource] = {"current": used, "limit": limit}

        return UserEntitlements(
            plan=config.id,
            features=list(config.features),
            quotas=quotas,
            usage=dict(usage),
            tier=config.tier,
            display_name=config.name,
            over_capacity=over_capacity,
        )

    @classmethod
    async def reset_monthly_quotas(cls, user_id: str):
        """Clear the CURRENT period's meters for a user.

        Rarely needed: a meter key carries its period, so a new month is already a fresh key.
        This exists for the support case of granting a user their month back, and for the
        scheduler that historically called it. It deletes only metered resources — a counted
        resource has no counter to reset.
        """
        period = usage_period()
        for resource in METERED_RESOURCES:
            await redis_manager.delete(cls._meter_key(user_id, resource, period))
        logger.info("Reset monthly meters for user %s period %s", user_id, period)


# ══════════════════════════════════════════════════════════════════════════
# The auditable matrix
# ══════════════════════════════════════════════════════════════════════════

def _build_plan_limits() -> Dict[str, Dict[str, int]]:
    """``{tier: {resource: limit}}`` — a DERIVED index of the catalogue, not a second copy.

    Exposed because a limit matrix keyed by the ladder vocabulary is what a reviewer and a test
    want to read. It is generated from :attr:`SubscriptionEngine._PLANS` at import time, so it
    cannot drift from the catalogue: there is nothing here to keep in sync by hand.
    """
    matrix: Dict[str, Dict[str, int]] = {}
    for config in SubscriptionEngine.get_all_plans():
        matrix[config.tier] = {
            resource.value: SubscriptionEngine.get_quota_limit(config.id, resource.value)
            for resource in Resource
        }
    return matrix


def _build_plan_features() -> Dict[str, Tuple[str, ...]]:
    """``{tier: (feature, …)}`` — the derived feature half of the matrix."""
    return {
        config.tier: tuple(config.features)
        for config in SubscriptionEngine.get_all_plans()
    }


#: The limit matrix, keyed by ladder tier. Derived; see :func:`_build_plan_limits`.
PLAN_LIMITS: Dict[str, Dict[str, int]] = _build_plan_limits()

#: The feature matrix, keyed by ladder tier. Derived; see :func:`_build_plan_features`.
PLAN_FEATURES: Dict[str, Tuple[str, ...]] = _build_plan_features()
