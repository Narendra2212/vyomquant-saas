"""
tests/test_pricing_ladder.py — the auditable plan → entitlement matrix.

WHAT THIS FILE IS FOR
=====================
One place a reviewer can read to answer "what does each plan actually allow?", asserted against
the code that enforces it rather than against a document. Every number below is transcribed from
the published pricing table, NOT from ``subscription_engine.py`` — if the two disagree, this file
fails, which is the whole point. A test that read the catalogue to check the catalogue would pass
no matter what the catalogue said.

The ladder, and the identifier trap it is built around:

    stored id       tier key        published name    monthly   annual
    ------------    ------------    --------------    -------   --------
    free            FREE            Free              ₹0        —
    starter         TRADER          Trader            ₹499      ₹4,990
    pro             PRO_QUANT       Pro Quant         ₹999      ₹9,990
    enterprise      BUSINESS        Business          ₹2,499    ₹24,990
    scale           ENTERPRISE      Enterprise        quoted    quoted

``enterprise`` is the id of the **Business** plan. That is deliberate: it was the historic ₹2,499
top tier and ``profiles.subscription_tier`` holds it for live subscribers, so the name moved and
the identifier did not. :class:`TestLegacyIdentifiersAreSafe` is the test that matters most in this
file — it asserts no stored value moves a paying customer onto a plan they did not buy.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from backend_app.backend.marketplace import money
from backend_app.core.schemas import BillingInterval, CheckoutRequest, SubscriptionTier
from backend_app.core.subscription_dependencies import (
    GATEABLE_FEATURES,
    build_feature_refusal,
    build_quota_refusal,
)
from backend_app.core.subscription_engine import (
    CREATOR_REVENUE_SHARE_PERCENT,
    CUSTOM_LIMIT,
    COUNTED_RESOURCES,
    METERED_RESOURCES,
    PLAN_LIMITS,
    PRICE_BASE_CURRENCY,
    Feature,
    Plan,
    PlanTier,
    Resource,
    SubscriptionEngine,
    usage_period,
)
from backend_app.core.usage_ledger import UNCOUNTABLE_RESOURCES

# ══════════════════════════════════════════════════════════════════════════
# The published table, transcribed. The single source of EXPECTATION.
# ══════════════════════════════════════════════════════════════════════════

#: tier key → stored plan id. The mapping a migration must never break.
TIER_TO_ID = {
    "FREE": "free",
    "TRADER": "starter",
    "PRO_QUANT": "pro",
    "BUSINESS": "enterprise",
    "ENTERPRISE": "scale",
}

#: The published capacity table, exactly as the pricing page states it.
#: Column order: FREE, TRADER, PRO_QUANT, BUSINESS.
PUBLISHED_LIMITS = {
    "strategies": (1, 3, 10, 25),
    "paper_strategies": (1, 3, 10, 25),
    "bots": (0, 3, 10, 25),                      # "live strategies"
    "exchange_connections": (1, 2, 5, 8),
    "trading_accounts": (1, 1, 3, 5),
    "backtests": (10, 100, 500, 1500),
    "custom_indicators": (3, 10, 30, 75),
    "strategy_versions": (3, 10, 25, 50),
    "ml_models": (0, 0, 5, 15),
    "ml_trainings": (0, 0, 50, 200),
    "optimizations": (0, 25, 100, 400),
    "marketplace_subscriptions": (0, 3, 10, 25),
    "marketplace_published": (0, 0, 5, 15),
}

#: The four self-serve tiers, in ladder order, matching PUBLISHED_LIMITS' column order.
SELF_SERVE_TIERS = ("FREE", "TRADER", "PRO_QUANT", "BUSINESS")

#: Published monthly price in minor units (paise), per tier.
PUBLISHED_MONTHLY_INR = {"FREE": 0, "TRADER": 49900, "PRO_QUANT": 99900, "BUSINESS": 249900}

#: Published annual price in minor units (paise), per tier. Free has none.
PUBLISHED_ANNUAL_INR = {"TRADER": 499000, "PRO_QUANT": 999000, "BUSINESS": 2499000}


def plan_id(tier: str) -> str:
    return TIER_TO_ID[tier]


def run(coro):
    """Drive one coroutine. Avoids a pytest-asyncio marker dependency for the few async calls."""
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ══════════════════════════════════════════════════════════════════════════
# 1. The capacity matrix
# ══════════════════════════════════════════════════════════════════════════


class TestPublishedCapacityMatrix:
    """Every figure the pricing page states is the figure the engine enforces."""

    @pytest.mark.parametrize("resource,expected", sorted(PUBLISHED_LIMITS.items()))
    def test_limit_matches_published_table(self, resource, expected):
        for tier, published in zip(SELF_SERVE_TIERS, expected):
            actual = SubscriptionEngine.get_quota_limit(plan_id(tier), resource)
            assert actual == published, (
                f"{tier} {resource}: published {published}, engine enforces {actual}"
            )

    def test_plan_limits_matrix_is_keyed_by_tier_and_agrees(self):
        """The exported PLAN_LIMITS index agrees with the catalogue it is derived from."""
        assert set(PLAN_LIMITS) == set(TIER_TO_ID)
        for tier, resource_limits in PLAN_LIMITS.items():
            for resource, limit in resource_limits.items():
                assert limit == SubscriptionEngine.get_quota_limit(plan_id(tier), resource)

    def test_every_resource_is_classified_as_counted_or_metered(self):
        """A resource in neither list would be declared and never enforced."""
        classified = set(COUNTED_RESOURCES) | set(METERED_RESOURCES)
        assert {r.value for r in Resource} == classified

    def test_the_three_monthly_allowances_are_the_metered_ones(self):
        assert set(METERED_RESOURCES) == {"backtests", "optimizations", "ml_trainings"}

    def test_enterprise_capacity_is_custom_not_unlimited(self):
        """The custom tier must not resolve to an uncapped allowance.

        `-1` in the catalogue means "agreed per contract". An Enterprise account with no override
        recorded is enforced at the BUSINESS figure, so an unprovisioned agreement cannot become an
        uncapped one — the failure mode "we forgot to set their limits" must be safe.
        """
        scale = SubscriptionEngine.get_plan_config("scale")
        assert scale.is_custom_priced is True
        assert all(v == CUSTOM_LIMIT for v in scale.quotas.values())

        for resource, expected in PUBLISHED_LIMITS.items():
            business_figure = expected[3]
            assert SubscriptionEngine.get_quota_limit("scale", resource) == business_figure

    def test_contractual_override_raises_but_never_lowers(self):
        """An override may raise a contracted account's capacity and may not downgrade it."""
        raised = SubscriptionEngine.get_effective_quotas("scale", {"strategies": 500})
        assert raised["strategies"] == 500

        # Below the plan's own floor: ignored, because a JSONB column must not be able to silently
        # strip capacity from a paying account.
        lowered = SubscriptionEngine.get_effective_quotas("enterprise", {"strategies": 2})
        assert lowered["strategies"] == 25

        # Garbage is ignored rather than crashing an entitlement check.
        junk = SubscriptionEngine.get_effective_quotas("pro", {"strategies": "lots", "nope": 5})
        assert junk["strategies"] == 10


# ══════════════════════════════════════════════════════════════════════════
# 2. The feature matrix
# ══════════════════════════════════════════════════════════════════════════


class TestFeatureMatrix:
    """The FEATURE → PLAN matrix the pricing brief specifies, asserted per plan."""

    @pytest.mark.parametrize(
        "feature,tiers_with_it",
        [
            (Feature.STRATEGY_BUILDER, ("FREE", "TRADER", "PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.BACKTESTING, ("FREE", "TRADER", "PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.PAPER_TRADING, ("FREE", "TRADER", "PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.MARKETPLACE_BROWSE, ("FREE", "TRADER", "PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.HISTORICAL_DATA, ("FREE", "TRADER", "PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.TICK_DATA, ("FREE", "TRADER", "PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.ALERTS, ("FREE", "TRADER", "PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.LIVE_TRADING, ("TRADER", "PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.ADVANCED_RISK, ("TRADER", "PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.OPTIMIZATION, ("TRADER", "PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.MARKETPLACE_SUBSCRIBE, ("TRADER", "PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.ML_TRAINING, ("PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.ML_NODES, ("PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.MARKETPLACE_PUBLISH, ("PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.CREATOR_REVENUE, ("PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.PORTFOLIO_RISK, ("PRO_QUANT", "BUSINESS", "ENTERPRISE")),
            (Feature.ADVANCED_PORTFOLIO_RISK, ("BUSINESS", "ENTERPRISE")),
            (Feature.DEDICATED_EXECUTION, ("BUSINESS", "ENTERPRISE")),
            (Feature.AUDIT_HISTORY, ("BUSINESS", "ENTERPRISE")),
        ],
    )
    def test_feature_availability(self, feature, tiers_with_it):
        for tier in TIER_TO_ID:
            expected = tier in tiers_with_it
            actual = SubscriptionEngine.has_feature(plan_id(tier), feature.value)
            assert actual is expected, (
                f"{tier}: {feature.value} should be {expected}, engine says {actual}"
            )

    def test_api_access_is_not_a_pricing_dimension_on_any_plan(self):
        """The product has no customer API tier, so no plan may advertise one."""
        for tier in TIER_TO_ID:
            assert SubscriptionEngine.has_feature(plan_id(tier), "api_access") is False

    def test_deprecated_feature_spellings_still_resolve(self):
        """Existing callers asking for a retired flag name keep getting a correct answer."""
        # `marketplace_access` used to mean "may transact" and was withheld from Free. It now
        # resolves to browse, which every plan has — the transacting gates have their own flags.
        assert SubscriptionEngine.has_feature("free", "marketplace_access") is True
        assert SubscriptionEngine.has_feature("free", "unlimited_backtesting") is True
        assert SubscriptionEngine.has_feature("free", "unlimited_builder") is True

    def test_every_gateable_feature_exists_in_the_catalogue(self):
        """A gateable feature no plan grants would render a permanently-locked panel."""
        for feature in GATEABLE_FEATURES:
            assert SubscriptionEngine.minimum_plan_for_feature(feature) is not None


# ══════════════════════════════════════════════════════════════════════════
# 3. The marketplace rules
# ══════════════════════════════════════════════════════════════════════════


class TestMarketplaceRules:
    """Browse everywhere, subscribe from Trader, publish from Pro Quant."""

    @pytest.mark.parametrize(
        "tier,browse,subscribe,publish,sub_cap,listing_cap,share",
        [
            ("FREE", True, False, False, 0, 0, 0),
            ("TRADER", True, True, False, 3, 0, 0),
            ("PRO_QUANT", True, True, True, 10, 5, 90),
            ("BUSINESS", True, True, True, 25, 15, 90),
        ],
    )
    def test_marketplace_matrix(
        self, tier, browse, subscribe, publish, sub_cap, listing_cap, share
    ):
        pid = plan_id(tier)
        assert SubscriptionEngine.has_feature(pid, Feature.MARKETPLACE_BROWSE.value) is browse
        assert SubscriptionEngine.has_feature(pid, Feature.MARKETPLACE_SUBSCRIBE.value) is subscribe
        assert SubscriptionEngine.has_feature(pid, Feature.MARKETPLACE_PUBLISH.value) is publish
        assert SubscriptionEngine.get_quota_limit(pid, "marketplace_subscriptions") == sub_cap
        assert SubscriptionEngine.get_quota_limit(pid, "marketplace_published") == listing_cap
        assert SubscriptionEngine.get_plan_config(pid).creator_revenue_share_percent == share

    def test_publishing_starts_at_pro_quant(self):
        assert SubscriptionEngine.minimum_plan_for_feature(
            Feature.MARKETPLACE_PUBLISH.value
        ) == "pro"

    def test_subscribing_starts_at_trader(self):
        assert SubscriptionEngine.minimum_plan_for_feature(
            Feature.MARKETPLACE_SUBSCRIBE.value
        ) == "starter"

    def test_creator_share_matches_the_arithmetic_that_settles_it(self):
        """The catalogue's advertised share and the money module's constant are one number.

        Two sources for the creator's percentage would be two numbers that can drift — and the one
        a trader reads on the pricing page would not be the one that pays them.
        """
        assert CREATOR_REVENUE_SHARE_PERCENT == money.OWNER_SHARE_PERCENT == 90

    def test_the_split_conserves_the_payment(self):
        """90/10 is computed as a share plus the integer RESIDUAL, so nothing is unaccounted."""
        for amount in (1, 7, 99, 100, 49900, 99900, 249900, 2499000):
            owner, platform = money.split_ninety_ten(amount)
            assert owner + platform == amount
            assert owner == (amount * 90) // 100


# ══════════════════════════════════════════════════════════════════════════
# 4. Pricing
# ══════════════════════════════════════════════════════════════════════════


class TestPublishedPricing:
    """₹0 / ₹499 / ₹999 / ₹2,499 monthly and ₹4,990 / ₹9,990 / ₹24,990 annually."""

    @pytest.mark.parametrize("tier,paise", sorted(PUBLISHED_MONTHLY_INR.items()))
    def test_monthly_inr(self, tier, paise):
        assert SubscriptionEngine.get_plan_config(plan_id(tier)).pricing["INR"] == paise

    @pytest.mark.parametrize("tier,paise", sorted(PUBLISHED_ANNUAL_INR.items()))
    def test_annual_inr(self, tier, paise):
        assert SubscriptionEngine.get_plan_config(plan_id(tier)).pricing_annual["INR"] == paise

    def test_annual_is_ten_months_for_twelve(self):
        for tier, annual in PUBLISHED_ANNUAL_INR.items():
            assert annual == PUBLISHED_MONTHLY_INR[tier] * 10

    def test_free_is_zero_and_enterprise_is_quoted(self):
        assert SubscriptionEngine.get_plan_config("free").pricing["INR"] == 0
        scale = SubscriptionEngine.get_plan_config("scale")
        assert scale.pricing == {}
        assert scale.is_custom_priced is True

    def test_published_inr_price_is_what_the_gateway_is_asked_for(self):
        """The regression this fixes: ₹999 was being charged as an FX conversion of $10 (~₹865).

        `localize_plan_price` must charge the PUBLISHED figure in the base currency, so the pricing
        page, the billing page and Razorpay all quote one number.
        """
        from backend_app.core.fx_service import FXService

        for tier, paise in PUBLISHED_MONTHLY_INR.items():
            if paise == 0:
                continue
            config = SubscriptionEngine.get_plan_config(plan_id(tier))
            priced = run(
                FXService.localize_plan_price(config.pricing, "INR", PRICE_BASE_CURRENCY)
            )
            assert priced.price_source == "published"
            assert priced.checkout_amount_minor == paise
            assert priced.localized_price == paise / 100
            assert priced.checkout_provider == "razorpay"

    def test_there_is_exactly_ONE_published_price_list(self):
        """Two base columns were two value points, and which one a customer got was a lottery.

        The catalogue published INR and USD independently — ₹499 and $5.00 — with nothing
        reconciling them. At the prevailing rate ₹499 is about $5.20, so they were ~4% apart, and
        every currency the catalogue did NOT publish was converted from the USD column. The
        marketing page advertised the rupee value point while every non-Indian visitor was quoted
        the dollar one.
        """
        assert PRICE_BASE_CURRENCY == "INR"
        for config in SubscriptionEngine.get_all_plans():
            if config.is_custom_priced:
                assert config.pricing == {}
                continue
            assert set(config.pricing) == {PRICE_BASE_CURRENCY}, (
                f"{config.id} publishes more than one base price: {config.pricing}. "
                "A second column is a second value point."
            )
            if config.pricing_annual:
                assert set(config.pricing_annual) <= {PRICE_BASE_CURRENCY}

    def test_every_currency_expresses_THE_SAME_value_point(self):
        """₹499, $5.20 and €4.58 must all be the same money, within a rounding unit.

        This is the property the two-column catalogue broke. It is asserted by converting each
        localised figure back to the base currency and comparing against the published price.
        """
        from backend_app.core.fx_service import FXService

        config = SubscriptionEngine.get_plan_config("pro")
        published_major = config.pricing[PRICE_BASE_CURRENCY] / 100

        for currency in ("INR", "USD", "EUR", "GBP", "AED", "SGD", "AUD", "JPY"):
            priced = run(
                FXService.localize_plan_price(config.pricing, currency, PRICE_BASE_CURRENCY)
            )
            back = run(
                FXService.convert_major(priced.localized_price, currency, PRICE_BASE_CURRENCY)
            )
            # Within one rupee: the only loss is rounding to the target currency's minor unit,
            # which for JPY (no minor unit) is the largest.
            assert abs(back - published_major) < 1.0, (
                f"{currency} is a different value point: {priced.localized_price} {currency} "
                f"is ₹{back:.2f} against a published ₹{published_major:.2f}"
            )

    def test_a_currency_with_no_published_figure_is_converted_and_says_so(self):
        """`price_source` is how a surface tells a price from an estimate."""
        from backend_app.core.fx_service import FXService

        config = SubscriptionEngine.get_plan_config("pro")
        priced = run(FXService.localize_plan_price(config.pricing, "EUR", PRICE_BASE_CURRENCY))
        assert priced.price_source == "fx"
        assert priced.checkout_currency == "EUR"
        assert priced.localized_price > 0

    def test_publishing_a_second_currency_makes_it_authoritative(self):
        """The extension point: a committed price point is charged verbatim, not converted.

        Asserted so the mechanism is known to work before anyone needs it — publishing a USD price
        point is a one-line catalogue change, and this is the proof it will be honoured rather than
        silently converted.
        """
        from backend_app.core.fx_service import FXService

        published = {PRICE_BASE_CURRENCY: 99900, "USD": 999}
        priced = run(FXService.localize_plan_price(published, "USD", PRICE_BASE_CURRENCY))
        assert priced.price_source == "published"
        assert priced.minor_units == 999
        assert priced.checkout_amount_minor == 999

    def test_free_consults_no_exchange_rate(self):
        """Zero is zero in every currency, and a rate failure must not make Free purchasable."""
        from backend_app.core.fx_service import FXService

        for currency in ("INR", "USD", "JPY"):
            priced = run(
                FXService.localize_plan_price({PRICE_BASE_CURRENCY: 0}, currency, PRICE_BASE_CURRENCY)
            )
            assert priced.localized_price == 0.0
            assert priced.checkout_amount_minor == 0


# ══════════════════════════════════════════════════════════════════════════
# 4b. The endpoint both price surfaces actually read
# ══════════════════════════════════════════════════════════════════════════


class TestThePublicPlansEndpoint:
    """``GET /api/billing/plans`` — the one payload the landing page and the billing page share.

    WHY THIS CLASS EXISTS AT THE HTTP LEVEL
    ---------------------------------------
    Everything above asserts the catalogue and ``FXService`` in isolation, and all of it passed
    while the two price surfaces disagreed in production: the landing page hardcoded ₹499 and the
    billing page localised off a second, unreconciled dollar column. The disagreement lived in the
    SEAM — in what the endpoint assembles out of the catalogue — which no service-level test
    touches. So this class drives the endpoint.

    It asserts the three properties a price list has to have, in every currency:

      1. the quoted figure and the charged figure are the same number;
      2. the figure converts back to the published rupee price, so every currency is one value
         point rather than an independent price list;
      3. the payload says which of the two it is, via ``price_source``.

    No authentication: the endpoint is public by design, because a visitor with no account has to
    be able to price the product. That is also asserted — a dependency added here would break the
    anonymous pricing page.
    """

    #: Display currencies spanning every branch the pricing code has: the published base, a
    #: Stripe-direct currency, a zero-decimal currency, and one with a multi-character symbol.
    CURRENCIES = ("INR", "USD", "EUR", "GBP", "JPY", "AED")

    @staticmethod
    def _client():
        """A client that does NOT run the application lifespan.

        ``with TestClient(app)`` would, and startup opens the Redis, database and exchange
        connections this suite has none of — it hangs rather than failing. The endpoint under test
        reads the catalogue and the FX table and touches no startup-managed resource, so the
        lifespan is not merely avoidable here, it is irrelevant. ``tests/test_user_router.py``
        constructs its client the same way and for the same reason.
        """
        from fastapi.testclient import TestClient

        from backend_app.main import app

        return TestClient(app)

    @classmethod
    def _plans(cls, currency: str) -> dict:
        response = cls._client().get("/api/billing/plans", params={"currency": currency})
        assert response.status_code == 200, response.text
        return response.json()

    def test_the_endpoint_needs_no_session(self):
        """A pricing page is read before anyone signs up."""
        assert self._client().get("/api/billing/plans").status_code == 200

    def test_it_offers_the_five_published_plans_in_ladder_order(self):
        payload = self._plans("INR")
        assert [plan["id"] for plan in payload["plans"]] == [
            "free",
            "starter",
            "pro",
            "enterprise",
            "scale",
        ]

    @pytest.mark.parametrize("currency", CURRENCIES)
    def test_it_reports_the_rupee_list_as_the_base_in_every_currency(self, currency):
        """``base_price`` is the PUBLISHED figure, and it does not change with the display currency.

        It used to be the USD column (`pricing_service.py` read ``plan.pricing["USD"]``), so a
        client reading ``base_price`` for a second-currency figure got the dollar value point —
        4% away from the rupee one the pricing page advertised.
        """
        payload = self._plans(currency)
        assert payload["base_currency"] == PRICE_BASE_CURRENCY

        by_id = {plan["id"]: plan for plan in payload["plans"]}
        for tier, paise in PUBLISHED_MONTHLY_INR.items():
            plan = by_id[plan_id(tier)]
            assert plan["base_currency"] == PRICE_BASE_CURRENCY
            assert plan["base_price"] == paise / 100, (
                f"{plan['id']} reports base_price {plan['base_price']} in {currency}; "
                f"the published figure is ₹{paise / 100}"
            )

    @pytest.mark.parametrize("currency", CURRENCIES)
    def test_the_figure_shown_is_the_figure_charged(self, currency):
        """A page quoting one amount while the gateway takes another is the whole defect."""
        payload = self._plans(currency)

        for plan in payload["plans"]:
            if plan["id"] == "scale":
                continue  # quoted, not listed
            if plan["is_direct_checkout"]:
                assert plan["checkout_price"] == plan["localized_price"], plan["id"]
                assert plan["checkout_currency"] == plan["currency"] == currency
            else:
                # An indirect currency is DISPLAYED in the visitor's currency and CHARGED in the
                # gateway's. Both figures are sent, so a surface can state the charge rather than
                # imply the display figure is it.
                assert plan["checkout_currency"] != currency
                assert plan["checkout_price"] >= 0

    @pytest.mark.parametrize("currency", CURRENCIES)
    def test_every_currency_is_the_same_money_as_the_published_price(self, currency):
        """₹499 and its dollar quote must be one value point, within a minor unit of rounding."""
        from backend_app.core.fx_service import FXService

        payload = self._plans(currency)
        by_id = {plan["id"]: plan for plan in payload["plans"]}

        for tier, paise in PUBLISHED_MONTHLY_INR.items():
            if paise == 0:
                continue
            plan = by_id[plan_id(tier)]
            back_in_rupees = run(
                FXService.convert_major(plan["localized_price"], currency, PRICE_BASE_CURRENCY)
            )
            # One rupee of tolerance: the only loss is rounding the quote to the display
            # currency's own minor unit, which for JPY is a whole yen (~₹1.2).
            assert abs(back_in_rupees - paise / 100) <= 1.5, (
                f"{plan['id']} quotes {plan['localized_price']} {currency}, which is "
                f"₹{back_in_rupees:.2f} against a published ₹{paise / 100}"
            )

    @pytest.mark.parametrize("currency", CURRENCIES)
    def test_it_says_whether_a_figure_is_published_or_converted(self, currency):
        """`price_source` is how a surface tells a price from an estimate."""
        payload = self._plans(currency)
        expected = "published" if currency == PRICE_BASE_CURRENCY else "fx"

        for plan in payload["plans"]:
            if plan["id"] == "free":
                # Free is zero in every currency and consults no rate, so it is always published.
                assert plan["price_source"] == "published"
                continue
            if plan["id"] == "scale":
                continue
            assert plan["price_source"] == expected, plan["id"]

    @pytest.mark.parametrize("currency", CURRENCIES)
    def test_the_annual_figure_is_published_and_not_derivable_by_the_client(self, currency):
        """₹4,990 is not ₹499 × 12 × 0.83, so the server sends it rather than a discount rate."""
        payload = self._plans(currency)
        by_id = {plan["id"]: plan for plan in payload["plans"]}

        for tier in PUBLISHED_ANNUAL_INR:
            annual = by_id[plan_id(tier)]["annual"]
            assert annual is not None, f"{plan_id(tier)} has no annual payload"
            assert annual["localized_price"] > 0
            # The per-month equivalent is the SERVER's division, because a client dividing would
            # round differently from the charge in a zero-decimal currency.
            assert annual["monthly_equivalent"] > 0
            assert annual["savings_percent"] == 17

        # Free has no annual price and the custom tier has no published price at all. A payload
        # here would let a client render a yearly figure nothing would charge.
        assert by_id["free"]["annual"] is None
        assert by_id["scale"]["annual"] is None

    def test_the_custom_tier_quotes_nothing(self):
        payload = self._plans("USD")
        scale = next(plan for plan in payload["plans"] if plan["id"] == "scale")

        assert scale["is_custom_priced"] is True
        assert scale["base_price"] == 0
        assert scale["localized_price"] == 0
        assert scale["annual"] is None

    def test_it_offers_the_currencies_a_selector_can_show(self):
        """The landing page's currency control is populated from this list."""
        payload = self._plans("INR")
        offered = payload["supported_currencies"]

        assert isinstance(offered, list) and offered, "no display currencies were offered"
        codes = {entry["code"] for entry in offered}
        assert {"INR", "USD", "EUR", "GBP"} <= codes
        for entry in offered:
            assert entry["symbol"], f"{entry['code']} has no symbol to render"

    def test_it_reports_the_precision_each_currency_is_printed_at(self):
        """`decimals` is the server's, because `¥818.00` is a conversion artefact."""
        assert all(plan["decimals"] == 0 for plan in self._plans("JPY")["plans"])
        assert all(plan["decimals"] == 2 for plan in self._plans("INR")["plans"])


# ══════════════════════════════════════════════════════════════════════════
# 5. Identifier safety — the migration-compatibility test
# ══════════════════════════════════════════════════════════════════════════


class TestLegacyIdentifiersAreSafe:
    """No value that can sit in ``profiles.subscription_tier`` moves a customer's plan."""

    @pytest.mark.parametrize(
        "stored,expected_id",
        [
            # Current canonical values resolve to themselves.
            ("free", "free"),
            ("starter", "starter"),
            ("pro", "pro"),
            ("enterprise", "enterprise"),
            ("scale", "scale"),
            # The ₹2,499 historic top tier, under every past spelling, is the Business plan.
            ("elite", "enterprise"),
            ("elite_1999", "enterprise"),
            ("ENTERPRISE", "enterprise"),
            ("institutional", "enterprise"),
            ("business", "enterprise"),
            # Trader's past spellings.
            ("starter_499", "starter"),
            ("basic", "starter"),
            ("BASIC", "starter"),
            ("trader", "starter"),
            # Pro Quant's past spellings.
            ("pro_999", "pro"),
            ("professional", "pro"),
            ("PROFESSIONAL", "pro"),
            ("ml_addon", "pro"),
            ("pro_quant", "pro"),
        ],
    )
    def test_stored_value_resolves_to_the_plan_the_customer_bought(self, stored, expected_id):
        assert SubscriptionEngine.migrate_plan_key(stored) == expected_id

    def test_the_business_plan_keeps_the_historic_enterprise_identifier(self):
        """The rename is DISPLAY only. Renaming the stored id would orphan live subscribers."""
        business = SubscriptionEngine.get_plan_config("enterprise")
        assert business.id == "enterprise"
        assert business.tier == PlanTier.BUSINESS.value
        assert business.name == "Business"
        assert business.pricing["INR"] == 249900

    def test_the_custom_tier_takes_a_new_identifier(self):
        """`scale`, not `enterprise` — otherwise a stored `enterprise` would be ambiguous."""
        custom = SubscriptionEngine.get_plan_config("scale")
        assert custom.id == "scale"
        assert custom.tier == PlanTier.ENTERPRISE.value
        assert custom.name == "Enterprise"

    @pytest.mark.parametrize("garbage", ["", None, "nonsense", "pro_99", "tier_9000", "   "])
    def test_an_unrecognised_plan_value_falls_back_to_free(self, garbage):
        """Fails to the LEAST privileged plan. An unknown string must never grant capacity."""
        assert SubscriptionEngine.migrate_plan_key(garbage) == "free"

    def test_every_catalogue_plan_can_be_granted_by_the_billing_webhook(self):
        """A plan missing from VALID_ITEM_KEYS would take payment and grant nothing.

        `_apply_billing_entitlement` raises 400 on an unknown item_key, and the webhook is retried
        into the same 400 — money captured, entitlement never written, no error a customer sees.
        """
        from backend_app.routers.billing import VALID_ITEM_KEYS

        for plan in Plan:
            assert plan.value in VALID_ITEM_KEYS

    def test_every_catalogue_plan_passes_checkout_request_validation(self):
        """A plan missing from SubscriptionTier is a 422 before the router is reached."""
        wire_values = {member.value for member in SubscriptionTier}
        for plan in Plan:
            assert plan.value in wire_values
            CheckoutRequest(tier=plan.value, currency="INR")

    def test_checkout_request_still_rejects_malformed_tiers(self):
        for bad in ("pro_99", "enterprise_299", "scale_9000"):
            with pytest.raises(ValidationError):
                CheckoutRequest(tier=bad, currency="USD")


# ══════════════════════════════════════════════════════════════════════════
# 6. Billing interval
# ══════════════════════════════════════════════════════════════════════════


class TestBillingInterval:
    def test_default_is_monthly_so_existing_clients_are_unchanged(self):
        """A client that sends no interval must keep producing the monthly subscription it did."""
        assert CheckoutRequest(tier="pro", currency="INR").interval == BillingInterval.MONTH

    def test_annual_is_accepted(self):
        assert CheckoutRequest(tier="pro", currency="INR", interval="year").interval == (
            BillingInterval.YEAR
        )

    def test_an_unknown_interval_is_rejected(self):
        with pytest.raises(ValidationError):
            CheckoutRequest(tier="pro", currency="INR", interval="fortnight")


# ══════════════════════════════════════════════════════════════════════════
# 7. Structured refusals
# ══════════════════════════════════════════════════════════════════════════


class TestEntitlementRefusals:
    """A refused request must say which limit, how close, and which plan fixes it."""

    def test_strategy_limit_refusal_shape(self):
        refusal = build_quota_refusal("strategies", "starter", 3, 3).as_detail()
        assert refusal["code"] == "STRATEGY_LIMIT_REACHED"
        assert refusal["current"] == 3
        assert refusal["limit"] == 3
        assert refusal["required_plan"] == "pro"
        assert refusal["required_tier"] == "PRO_QUANT"
        assert "Trader" in refusal["message"]
        assert "Pro Quant" in refusal["upgrade_message"]
        assert "₹999" in refusal["cta_label"]
        assert refusal["contact_sales"] is False

    @pytest.mark.parametrize(
        "tier,expected_required_tier",
        [
            ("FREE", "TRADER"),
            ("TRADER", "PRO_QUANT"),
            ("PRO_QUANT", "BUSINESS"),
        ],
    )
    def test_each_plan_points_at_the_next_one_up(self, tier, expected_required_tier):
        limit = PUBLISHED_LIMITS["strategies"][SELF_SERVE_TIERS.index(tier)]
        refusal = build_quota_refusal("strategies", plan_id(tier), limit, limit).as_detail()
        assert refusal["required_tier"] == expected_required_tier

    def test_the_top_of_the_ladder_asks_for_a_conversation_not_an_upgrade(self):
        refusal = build_quota_refusal("strategies", "enterprise", 25, 25).as_detail()
        assert refusal["contact_sales"] is True
        assert refusal["cta_label"] == "Talk to Sales"

    def test_a_zero_allowance_says_not_included_rather_than_capacity_reached(self):
        """`0 / 0` is "your plan does not include this", which is different copy from "you're full"."""
        refusal = build_quota_refusal("marketplace_subscriptions", "free", 0, 0).as_detail()
        assert "not included" in refusal["message"].lower()
        assert refusal["required_tier"] == "TRADER"
        assert "₹499" in refusal["cta_label"]

    @pytest.mark.parametrize(
        "resource,plan,expected_code",
        [
            ("strategies", "free", "STRATEGY_LIMIT_REACHED"),
            ("bots", "free", "LIVE_STRATEGY_LIMIT_REACHED"),
            ("backtests", "free", "BACKTEST_LIMIT_REACHED"),
            ("optimizations", "free", "OPTIMIZATION_LIMIT_REACHED"),
            ("ml_trainings", "starter", "ML_TRAINING_LIMIT_REACHED"),
            ("ml_models", "starter", "ML_MODEL_LIMIT_REACHED"),
            ("marketplace_subscriptions", "starter", "MARKETPLACE_SUBSCRIPTION_LIMIT_REACHED"),
            ("marketplace_published", "pro", "MARKETPLACE_LISTING_LIMIT_REACHED"),
            ("exchange_connections", "free", "EXCHANGE_CONNECTION_LIMIT_REACHED"),
        ],
    )
    def test_refusal_codes_are_stable(self, resource, plan, expected_code):
        """Clients branch on these, so they are part of the API contract."""
        limit = SubscriptionEngine.get_quota_limit(plan, resource)
        assert build_quota_refusal(resource, plan, limit, limit).code == expected_code

    def test_marketplace_publishing_refusal_names_pro_quant(self):
        refusal = build_feature_refusal(Feature.MARKETPLACE_PUBLISH.value, "starter").as_detail()
        assert refusal["code"] == "MARKETPLACE_PUBLISH_NOT_INCLUDED"
        assert refusal["message"] == "Marketplace publishing starts with Pro Quant."
        assert refusal["required_plan"] == "pro"
        assert "₹999" in refusal["cta_label"]

    def test_marketplace_subscription_refusal_names_trader(self):
        refusal = build_feature_refusal(Feature.MARKETPLACE_SUBSCRIBE.value, "free").as_detail()
        assert refusal["message"] == "Marketplace subscriptions start with Trader."
        assert refusal["required_plan"] == "starter"

    def test_ml_refusal_names_pro_quant_for_free_and_trader(self):
        for plan in ("free", "starter"):
            refusal = build_feature_refusal(Feature.ML_TRAINING.value, plan).as_detail()
            assert refusal["code"] == "ML_NOT_INCLUDED"
            assert refusal["required_plan"] == "pro"

    def test_live_trading_refusal_names_trader_for_free(self):
        refusal = build_feature_refusal(Feature.LIVE_TRADING.value, "free").as_detail()
        assert refusal["code"] == "LIVE_TRADING_NOT_INCLUDED"
        assert refusal["required_plan"] == "starter"
        assert "₹499" in refusal["cta_label"]

    def test_every_refusal_carries_a_message_and_a_cta(self):
        """A refusal with no sentence is a 403 a trader cannot act on."""
        for feature in GATEABLE_FEATURES:
            refusal = build_feature_refusal(feature, "free").as_detail()
            assert refusal["message"].strip() != ""
            assert refusal["cta_label"].strip() != ""


# ══════════════════════════════════════════════════════════════════════════
# 8. Downgrade behaviour
# ══════════════════════════════════════════════════════════════════════════


class TestDowngradeIsNonDestructive:
    """A plan going down must preserve data and only prevent ADDING more."""

    def test_over_capacity_is_reported_not_enforced_by_deletion(self):
        """Pro Quant with 10 strategies downgraded to Trader: all 10 preserved, 7 over capacity."""
        usage = {"strategies": 10, "bots": 10, "marketplace_published": 5}
        entitlements = run(
            SubscriptionEngine.get_user_entitlements("u1", "starter", usage)
        )
        assert entitlements.plan == "starter"
        # The usage is reported verbatim. Nothing is clamped, zeroed or deleted.
        assert entitlements.usage["strategies"] == 10
        assert entitlements.over_capacity["strategies"] == {"current": 10, "limit": 3}
        assert entitlements.over_capacity["bots"] == {"current": 10, "limit": 3}
        assert entitlements.over_capacity["marketplace_published"] == {"current": 5, "limit": 0}

    def test_an_account_within_its_plan_reports_no_over_capacity(self):
        entitlements = run(
            SubscriptionEngine.get_user_entitlements("u2", "pro", {"strategies": 8})
        )
        assert entitlements.over_capacity == {}

    def test_creating_beyond_the_limit_is_refused_while_over_capacity(self):
        """Over-capacity prevents the NEXT one. It does not retroactively forbid what exists."""
        allowed = run(
            SubscriptionEngine.check_quota_entitlement("u1", "starter", "strategies", 10)
        )
        assert allowed is False

    def test_an_unreadable_usage_figure_is_not_reported_as_over_capacity(self):
        """A missing figure must not produce a warning about capacity nobody measured."""
        entitlements = run(
            SubscriptionEngine.get_user_entitlements("u3", "starter", {"strategies": None})
        )
        assert "strategies" not in entitlements.over_capacity


# ══════════════════════════════════════════════════════════════════════════
# 9. Usage periods
# ══════════════════════════════════════════════════════════════════════════


class TestUsagePeriod:
    """The month boundary is computed in one place and is part of the meter key."""

    def test_period_is_year_month(self):
        assert usage_period(datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)) == "2026-03"

    def test_january_and_december_are_zero_padded_and_distinct(self):
        assert usage_period(datetime(2026, 1, 1, tzinfo=timezone.utc)) == "2026-01"
        assert usage_period(datetime(2025, 12, 31, 23, 59, tzinfo=timezone.utc)) == "2025-12"

    def test_a_naive_timestamp_is_read_as_utc_rather_than_local(self):
        """Two callers disagreeing about a naive timestamp's zone is how a month gets double-billed."""
        assert usage_period(datetime(2026, 6, 15, 10, 0)) == "2026-06"

    def test_the_meter_key_carries_the_period(self):
        """A new month is a new key holding zero, so no scheduled reset is required."""
        key = SubscriptionEngine._meter_key("user-1", "backtests", "2026-03")
        assert key == "quota:user-1:backtests:2026-03"
        assert SubscriptionEngine._meter_key("user-1", "backtests", "2026-04") != key

    def test_the_meter_ttl_outlives_a_month(self):
        """A 24-hour TTL made the previous 'monthly' allowance reset daily."""
        assert SubscriptionEngine._METER_TTL_SECONDS > 31 * 24 * 60 * 60


# ══════════════════════════════════════════════════════════════════════════
# 10. Architectural honesty
# ══════════════════════════════════════════════════════════════════════════


class TestDeclaredGapsStayDeclared:
    """A limit with no source of truth must say so rather than report a fabricated zero."""

    def test_custom_indicators_is_declared_uncountable(self):
        """There is no per-account custom-indicator store, so there is no count to report.

        The limit stays in the catalogue so it is auditable and so the API reports it, and
        `usage_ledger.count` REFUSES to answer for it rather than returning 0 — which would render
        as `0 / 3` and read as a measurement.
        """
        assert "custom_indicators" in UNCOUNTABLE_RESOURCES

        from backend_app.core.usage_ledger import UNCOUNTABLE_REASONS, UsageReadFailed, count

        assert "custom_indicators" in UNCOUNTABLE_REASONS
        with pytest.raises(UsageReadFailed):
            run(count("custom_indicators", "u1", MagicMock()))

    def test_strategy_versions_is_scoped_per_strategy_not_per_account(self):
        from backend_app.core.subscription_engine import PER_PARENT_RESOURCES

        assert "strategy_versions" in PER_PARENT_RESOURCES


class TestTheQuotaReleaseIsTheColumn:
    """Deleting a counted resource must not also write a counter.

    The pair to ``tests/test_task_5_1_strategy_archive.py``'s release assertion. That file checks
    the route calls ``decrement_usage`` once when a strategy is archived; this one checks what the
    call does — nothing, for a counted resource, because the count comes from the table and a
    counter beside it could only drift from it.
    """

    def test_decrement_usage_writes_no_counter_for_a_counted_resource(self):
        from unittest.mock import AsyncMock, patch

        from backend_app.core.subscription_dependencies import decrement_usage

        with patch.object(
            SubscriptionEngine, "decrement_quota_usage", new=AsyncMock(return_value=0)
        ) as counter:
            result = run(decrement_usage(Resource.STRATEGIES.value, {"id": "u1"}))

        assert result == 0
        assert counter.await_count == 0, (
            "a counted resource must not maintain a counter: the count is read from the "
            "persistence layer, and a second record of it can only drift"
        )

    def test_decrement_usage_does_release_a_metered_reservation(self):
        """The narrow case it IS for: a reservation whose work never started."""
        from unittest.mock import AsyncMock, patch

        from backend_app.core.subscription_dependencies import decrement_usage

        with patch.object(
            SubscriptionEngine, "decrement_quota_usage", new=AsyncMock(return_value=3)
        ) as counter:
            result = run(decrement_usage(Resource.BACKTESTS.value, {"id": "u1"}))

        assert result == 3
        assert counter.await_count == 1


class TestSchemaSkewCannotTakeTheGatesDown:
    """Migration 017's columns are read and written, so the code must survive their absence.

    ``plan_limit_overrides`` and ``billing_interval`` arrive with
    ``017_plan_entitlements.sql``. Code can reach a database that has not run it — a deploy landing
    before the migration, a replica mid-rollout, a developer's own Supabase project. PostgREST
    answers a request for an unknown column with ``42703``, which fails the WHOLE statement rather
    than just the new column.

    Two of those statements are load-bearing, and neither may depend on the deploy order:

      * the plan READ, which fails closed in production — so a missing column would otherwise mean
        a 500 on every gated route;
      * the entitlement WRITE, which runs AFTER the customer has been charged — so a missing column
        would otherwise mean money captured and no plan granted, on every webhook retry.
    """

    @staticmethod
    def _profiles_double(reject_columns: str):
        """A Supabase double that refuses any select naming ``reject_columns``."""
        from unittest.mock import MagicMock

        sb = MagicMock()

        def table(_name):
            query = MagicMock()
            state = {"columns": ""}

            def select(columns):
                state["columns"] = columns
                return query

            def execute():
                if reject_columns in state["columns"]:
                    raise RuntimeError(
                        f'42703 column profiles.{reject_columns} does not exist'
                    )
                return MagicMock(
                    data=[{"subscription_tier": "pro", "subscription_status": "active"}],
                    error=None,
                )

            query.select.side_effect = select
            query.eq.return_value = query
            query.execute.side_effect = execute
            return query

        sb.table.side_effect = table
        return sb

    def test_the_plan_is_still_resolved_without_plan_limit_overrides(self):
        """An un-migrated database must not strip a paying account of its plan."""
        from unittest.mock import AsyncMock, patch

        from backend_app.core.subscription_dependencies import get_plan_context

        sb = self._profiles_double("plan_limit_overrides")

        with patch(
            "backend_app.core.subscription_dependencies.redis_manager.get",
            new=AsyncMock(return_value=None),
        ), patch(
            "backend_app.core.subscription_dependencies.redis_manager.set",
            new=AsyncMock(return_value=None),
        ), patch.dict("os.environ", {"ENV": "production"}):
            context = run(get_plan_context("u-skew", sb))

        assert context.plan == "pro"
        assert context.tier == "PRO_QUANT"
        # No overrides to apply, which is the truth: the column that would hold them is absent.
        assert dict(context.overrides) == {}

    def test_a_genuinely_unreadable_profile_still_fails_closed_in_production(self):
        """The retry must not become a swallow. An unreadable profile is not a Free account."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from backend_app.core.subscription_dependencies import get_plan_context

        sb = MagicMock()

        def table(_name):
            query = MagicMock()
            query.select.return_value = query
            query.eq.return_value = query
            query.execute.side_effect = RuntimeError("connection reset")
            return query

        sb.table.side_effect = table

        with patch(
            "backend_app.core.subscription_dependencies.redis_manager.get",
            new=AsyncMock(return_value=None),
        ), patch(
            "backend_app.core.subscription_dependencies.redis_manager.set",
            new=AsyncMock(return_value=None),
        ), patch.dict("os.environ", {"ENV": "production"}):
            with pytest.raises(RuntimeError):
                run(get_plan_context("u-broken", sb))

    def test_the_paid_tier_is_written_even_if_the_interval_column_is_absent(self):
        """Money was already taken. The tier may not be lost to a column that is not there yet."""
        from unittest.mock import MagicMock

        writes: list = []
        sb = MagicMock()

        def table(_name):
            query = MagicMock()
            state: dict = {}

            def update(payload):
                state["payload"] = payload
                return query

            def execute():
                payload = state.get("payload")
                if payload is None:
                    # The `select` the handler makes for the previous plan.
                    return MagicMock(data=[{"subscription_tier": "free"}], error=None)
                if "billing_interval" in payload:
                    raise RuntimeError(
                        "42703 column profiles.billing_interval does not exist"
                    )
                writes.append(payload)
                return MagicMock(data=[payload], error=None)

            query.select.return_value = query
            query.eq.return_value = query
            query.update.side_effect = update
            query.execute.side_effect = execute
            return query

        sb.table.side_effect = table

        from unittest.mock import AsyncMock, patch

        import backend_app.routers.billing as billing

        with patch.object(billing, "_background_sb", return_value=sb), patch.object(
            billing, "invalidate_profile_cache", new=AsyncMock(return_value=None)
        ), patch.object(
            billing.RealtimeSync, "sync_subscription_change", new=AsyncMock(return_value=None)
        ):
            run(
                billing._apply_billing_entitlement(
                    "u-paid", "pro", metadata={"interval": "year"}
                )
            )

        # Exactly one surviving write, carrying the tier and not the interval.
        assert writes == [{"subscription_tier": "pro"}]
