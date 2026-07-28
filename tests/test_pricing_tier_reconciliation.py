"""
tests/test_pricing_tier_reconciliation.py

Test suite verifying the reconciliation of subscription tiers between frontend display and backend enforcement.
- Validates SubscriptionTier enum values ('free', 'pro_999', 'elite_1999')
- Validates CheckoutRequest schema accepts valid tiers and rejects stale strings ('starter', 'pro_99', 'enterprise')
- Verifies DEPLOYMENT_LIMITS and ML_BUILD_LIMITS key consistency
"""

import pytest
from pydantic import ValidationError

from backend_app.core.schemas import SubscriptionTier, CheckoutRequest
from backend_app.core.dependencies import DEPLOYMENT_LIMITS, ML_BUILD_LIMITS


class TestPricingTierReconciliation:

    def test_subscription_tier_enum_values(self):
        assert SubscriptionTier.FREE.value == "free"
        assert SubscriptionTier.PRO.value == "pro_999"
        assert SubscriptionTier.ELITE.value == "elite_1999"

    def test_checkout_request_valid_tiers(self):
        req_free = CheckoutRequest(tier=SubscriptionTier.FREE, currency="INR")
        req_pro = CheckoutRequest(tier=SubscriptionTier.PRO, currency="INR")
        req_elite = CheckoutRequest(tier=SubscriptionTier.ELITE, currency="USD")

        assert req_free.tier == "free"
        assert req_pro.tier == "pro_999"
        assert req_elite.tier == "elite_1999"

    def test_checkout_request_rejects_stale_tiers(self):
        with pytest.raises(ValidationError):
            CheckoutRequest(tier="starter", currency="USD")

        with pytest.raises(ValidationError):
            CheckoutRequest(tier="pro_99", currency="USD")

        with pytest.raises(ValidationError):
            CheckoutRequest(tier="enterprise_299", currency="USD")

    def test_limit_dictionaries_key_alignment(self):
        expected_keys = {"free", "pro_999", "elite_1999"}
        assert set(DEPLOYMENT_LIMITS.keys()) == expected_keys
        assert set(ML_BUILD_LIMITS.keys()) == expected_keys

        assert DEPLOYMENT_LIMITS["free"] == 1
        assert DEPLOYMENT_LIMITS["pro_999"] == 5
        assert DEPLOYMENT_LIMITS["elite_1999"] == float("inf")

        assert ML_BUILD_LIMITS["free"] == 0
        assert ML_BUILD_LIMITS["pro_999"] == 0
        assert ML_BUILD_LIMITS["elite_1999"] == 2
