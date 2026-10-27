"""
core/billing_lifecycle.py — the two billing-lifecycle operations that are real.

THIS MODULE USED TO ADVERTISE A LIFECYCLE IT DID NOT HAVE. The docstring listed trial
management, activation, upgrade/downgrade, expiration, renewal and grace period, and nine
of its functions had **no production caller at all** -- they were reachable only from tests
and one attack script. Read together, the file described a subscription system far more
complete than the one that ran, which is the most expensive kind of wrong comment: it
invites a reader to assume a trial exists, or that a scheduled downgrade will execute.

What was removed, and what it would have done if anything had called it:

  * ``start_trial`` / ``calculate_trial_end_date`` / ``check_trial_expiry`` and
    ``TRIAL_DAYS = 14`` -- there is no trial in this product. Nothing ever wrote
    ``SubscriptionStatus.TRIAL``.
  * ``calculate_grace_period_end_date`` / ``check_subscription_expiry`` and
    ``GRACE_PERIOD_DAYS = 7`` -- there is no grace period. Nothing ever wrote
    ``SubscriptionStatus.GRACE_PERIOD``.
  * ``activate_subscription`` -- the webhook writes ``profiles`` directly instead.
  * ``upgrade_subscription`` -- an upgrade is a fresh checkout whose webhook overwrites the
    tier. There was never any proration.
  * ``downgrade_subscription`` -- wrote ``profiles.pending_downgrade_tier``, a column
    **nothing anywhere reads**. A scheduled downgrade would never have executed.
  * ``renew_subscription`` -- its only caller was the Stripe ``invoice.payment_succeeded``
    handler, removed with the provider. Razorpay orders do not recur, so nothing renews.
  * ``calculate_next_billing_date`` -- AND THIS ONE CARRIED A MONEY BUG. It did
    ``current_date.replace(month=current_date.month + 1)``, which raises
    ``ValueError: day is out of range for month`` on the 29th/30th/31st rolling into a
    shorter month. It was live on the Stripe renewal path, the exception was swallowed by
    the handler's ``except``, and the webhook still reported success -- so roughly seven
    days a year a customer was charged and the renewal was silently not recorded. The
    calendar-safe ``add_billing_period`` below already existed beside it and is what the
    Razorpay path always used. Removing Stripe removed the bug's only caller; removing the
    function removes the bug.

WHAT REMAINS IS WHAT HAS CALLERS: ``add_billing_period`` (``routers/billing.py``) and
``cancel_subscription`` (``routers/billing.py``). If a recurring gateway is added later,
build its renewal on ``add_billing_period`` and do not resurrect the function above.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from backend_app.core.cache import redis_manager
from backend_app.core.subscription_engine import Plan

logger = logging.getLogger("BillingLifecycle")


class SubscriptionStatus:
    """Subscription status constants.

    ``TRIAL`` and ``GRACE_PERIOD`` are retained as vocabulary but NOTHING WRITES THEM --
    the code that would have is gone (see the module docstring). They are kept rather than
    deleted because they are plain strings that other modules may compare against, and a
    missing attribute is a harder failure than an unused one. Do not read their presence as
    evidence that either state exists.
    """
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    GRACE_PERIOD = "grace_period"


class BillingLifecycle:
    """Billing lifecycle manager."""

    @staticmethod
    def add_billing_period(start: datetime, interval: Optional[str]) -> datetime:
        """End of one paid period starting at *start* (BILL-01).

        ``interval`` is ``"year"`` or anything else (monthly). Calendar-safe: Jan 31 + 1 month
        is Feb 28/29, not a ValueError (``calculate_next_billing_date`` cannot do that).
        """
        import calendar

        months = 12 if interval == "year" else 1
        total = start.month - 1 + months
        year, month = start.year + total // 12, total % 12 + 1
        day = min(start.day, calendar.monthrange(year, month)[1])
        return start.replace(year=year, month=month, day=day)

    
    
    
    
    @staticmethod
    async def cancel_subscription(
        user_id: str,
        cancel_at_period_end: bool = True,
        supabase: Any = None,
    ) -> Dict[str, Any]:
        """
        Cancel subscription.
        If cancel_at_period_end is True, subscription remains active until billing date.
        If False, subscription is cancelled immediately.
        """
        try:
            if cancel_at_period_end:
                # Soft cancel - mark for cancellation at period end
                update_data = {
                    "subscription_status": SubscriptionStatus.CANCELLED,
                    "cancel_at_period_end": True,
                }
                message = "Subscription will be cancelled at the end of the current billing period"
            else:
                # Immediate cancel - downgrade to free
                update_data = {
                    "subscription_tier": Plan.FREE.value,
                    "subscription_status": SubscriptionStatus.CANCELLED,
                    "cancel_at_period_end": False,
                    "next_billing_date": None,
                }
                message = "Subscription cancelled immediately"
            
            resp = (
                supabase.table("profiles")
                .update(update_data)
                .eq("id", user_id)
                .execute()
            )
            
            # Invalidate profile cache
            await redis_manager.delete(f"profile_limits:{user_id}")
            
            logger.info(f"Subscription cancelled for user {user_id}: {message}")
            
            return {
                "status": "cancelled",
                "cancel_at_period_end": cancel_at_period_end,
                "message": message,
            }
        except Exception as e:
            logger.error(f"Failed to cancel subscription for user {user_id}: {e}")
            raise
    
    
    
    
    
