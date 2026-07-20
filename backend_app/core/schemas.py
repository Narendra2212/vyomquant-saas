# core/schemas.py

from enum import Enum
from pydantic import BaseModel


class SubscriptionTier(str, Enum):
    FREE = "free"
    PRO = "pro_999"
    ELITE = "elite_1999"


class CheckoutRequest(BaseModel):
    tier: SubscriptionTier
    currency: str = "INR"  # "INR" triggers Razorpay, "USD" triggers Stripe
    is_addon: bool = False  # Set to true if buying the 199 INR ML strategy addon


class UserLimits(BaseModel):
    tier: SubscriptionTier
    deployed_bots_count: int
    ml_strategies_built: int
    allowed_deployments: int
    allowed_ml_builds: int
