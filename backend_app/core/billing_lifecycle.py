"""
core/billing_lifecycle.py — Billing Lifecycle Management

Handles the complete billing lifecycle:
- Trial management
- Subscription activation
- Upgrade/downgrade
- Cancellation
- Expiration
- Renewal
- Grace period
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from backend_app.core.cache import redis_manager
from backend_app.core.subscription_engine import Plan, SubscriptionEngine

logger = logging.getLogger("BillingLifecycle")


class SubscriptionStatus:
    """Subscription status constants."""
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    GRACE_PERIOD = "grace_period"


class BillingLifecycle:
    """Billing lifecycle manager."""
    
    TRIAL_DAYS = 14
    GRACE_PERIOD_DAYS = 7
    
    @staticmethod
    def calculate_next_billing_date(current_date: Optional[datetime] = None) -> datetime:
        """Calculate next billing date (1 month from now)."""
        if current_date is None:
            current_date = datetime.utcnow()
        
        # Add 1 month
        if current_date.month == 12:
            next_date = current_date.replace(year=current_date.year + 1, month=1)
        else:
            next_date = current_date.replace(month=current_date.month + 1)
        
        return next_date
    
    @staticmethod
    def calculate_trial_end_date(start_date: Optional[datetime] = None) -> datetime:
        """Calculate trial end date."""
        if start_date is None:
            start_date = datetime.utcnow()
        return start_date + timedelta(days=BillingLifecycle.TRIAL_DAYS)
    
    @staticmethod
    def calculate_grace_period_end_date(expiry_date: datetime) -> datetime:
        """Calculate grace period end date."""
        return expiry_date + timedelta(days=BillingLifecycle.GRACE_PERIOD_DAYS)
    
    @staticmethod
    async def start_trial(user_id: str, supabase: Any) -> Dict[str, Any]:
        """
        Start a trial subscription for a new user.
        Trial users get STARTER plan features for TRIAL_DAYS.
        """
        trial_end = BillingLifecycle.calculate_trial_end_date()
        
        try:
            # Update user profile with trial status
            resp = (
                supabase.table("profiles")
                .update({
                    "subscription_tier": Plan.STARTER.value,
                    "subscription_status": SubscriptionStatus.TRIAL,
                    "trial_end_date": trial_end.isoformat(),
                    "next_billing_date": trial_end.isoformat(),
                })
                .eq("id", user_id)
                .execute()
            )
            
            logger.info(f"Trial started for user {user_id}, ends {trial_end}")
            
            return {
                "status": "trial_started",
                "plan": Plan.STARTER.value,
                "trial_end_date": trial_end.isoformat(),
                "trial_days": BillingLifecycle.TRIAL_DAYS,
            }
        except Exception as e:
            logger.error(f"Failed to start trial for user {user_id}: {e}")
            raise
    
    @staticmethod
    async def activate_subscription(
        user_id: str,
        plan: str,
        supabase: Any,
        payment_provider: str = "stripe",
        payment_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Activate a paid subscription.
        """
        # Migrate old plan key if necessary
        plan = SubscriptionEngine.migrate_plan_key(plan)
        
        next_billing = BillingLifecycle.calculate_next_billing_date()
        
        try:
            # Update user profile
            update_data = {
                "subscription_tier": plan,
                "subscription_status": SubscriptionStatus.ACTIVE,
                "next_billing_date": next_billing.isoformat(),
                "trial_end_date": None,  # Clear trial end date
                "payment_provider": payment_provider,
            }
            
            if payment_id:
                update_data["payment_id"] = payment_id
            
            resp = (
                supabase.table("profiles")
                .update(update_data)
                .eq("id", user_id)
                .execute()
            )
            
            # Invalidate profile cache
            await redis_manager.delete(f"profile_limits:{user_id}")
            
            logger.info(f"Subscription activated for user {user_id}: plan={plan}, next_billing={next_billing}")
            
            return {
                "status": "active",
                "plan": plan,
                "next_billing_date": next_billing.isoformat(),
            }
        except Exception as e:
            logger.error(f"Failed to activate subscription for user {user_id}: {e}")
            raise
    
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
    
    @staticmethod
    async def renew_subscription(
        user_id: str,
        supabase: Any,
    ) -> Dict[str, Any]:
        """
        Renew subscription (auto-renewal).
        Called by payment webhook on successful recurring payment.
        """
        try:
            # Get current plan
            resp = (
                supabase.table("profiles")
                .select("subscription_tier")
                .eq("id", user_id)
                .execute()
            )
            
            if not resp.data:
                raise Exception("User profile not found")
            
            current_plan = resp.data[0].get("subscription_tier", Plan.FREE.value)
            
            # Calculate next billing date
            next_billing = BillingLifecycle.calculate_next_billing_date()
            
            # Update profile
            update_data = {
                "subscription_status": SubscriptionStatus.ACTIVE,
                "next_billing_date": next_billing.isoformat(),
                "cancel_at_period_end": False,
            }
            
            resp = (
                supabase.table("profiles")
                .update(update_data)
                .eq("id", user_id)
                .execute()
            )
            
            # Invalidate profile cache
            await redis_manager.delete(f"profile_limits:{user_id}")
            
            logger.info(f"Subscription renewed for user {user_id}: plan={current_plan}, next_billing={next_billing}")
            
            return {
                "status": "renewed",
                "plan": current_plan,
                "next_billing_date": next_billing.isoformat(),
            }
        except Exception as e:
            logger.error(f"Failed to renew subscription for user {user_id}: {e}")
            raise
    
    @staticmethod
    async def check_trial_expiry(user_id: str, supabase: Any) -> bool:
        """
        Check if trial has expired and downgrade if necessary.
        Returns True if trial was expired and downgraded.
        """
        try:
            resp = (
                supabase.table("profiles")
                .select("subscription_status", "trial_end_date")
                .eq("id", user_id)
                .execute()
            )
            
            if not resp.data:
                return False
            
            profile = resp.data[0]
            
            if profile.get("subscription_status") != SubscriptionStatus.TRIAL:
                return False
            
            trial_end = profile.get("trial_end_date")
            if not trial_end:
                return False
            
            trial_end_date = datetime.fromisoformat(trial_end)
            
            if datetime.utcnow() > trial_end_date:
                # Trial expired, downgrade to free
                await BillingLifecycle.cancel_subscription(
                    user_id,
                    cancel_at_period_end=False,
                    supabase=supabase,
                )
                logger.info(f"Trial expired for user {user_id}, downgraded to free")
                return True
            
            return False
        except Exception as e:
            logger.error(f"Failed to check trial expiry for user {user_id}: {e}")
            return False
    
    @staticmethod
    async def check_subscription_expiry(user_id: str, supabase: Any) -> bool:
        """
        Check if subscription has expired and handle grace period.
        Returns True if subscription was expired.
        """
        try:
            resp = (
                supabase.table("profiles")
                .select("subscription_status", "next_billing_date", "cancel_at_period_end")
                .eq("id", user_id)
                .execute()
            )
            
            if not resp.data:
                return False
            
            profile = resp.data[0]
            
            if profile.get("subscription_status") != SubscriptionStatus.ACTIVE:
                return False
            
            if not profile.get("cancel_at_period_end"):
                return False
            
            next_billing = profile.get("next_billing_date")
            if not next_billing:
                return False
            
            next_billing_date = datetime.fromisoformat(next_billing)
            grace_end = BillingLifecycle.calculate_grace_period_end_date(next_billing_date)
            
            if datetime.utcnow() > next_billing_date:
                if datetime.utcnow() <= grace_end:
                    # In grace period
                    await supabase.table("profiles").update({
                        "subscription_status": SubscriptionStatus.GRACE_PERIOD,
                    }).eq("id", user_id).execute()
                    logger.info(f"User {user_id} in grace period until {grace_end}")
                else:
                    # Grace period expired, downgrade to free
                    await BillingLifecycle.cancel_subscription(
                        user_id,
                        cancel_at_period_end=False,
                        supabase=supabase,
                    )
                    logger.info(f"Subscription expired for user {user_id}, downgraded to free")
                return True
            
            return False
        except Exception as e:
            logger.error(f"Failed to check subscription expiry for user {user_id}: {e}")
            return False
    
    @staticmethod
    async def upgrade_subscription(
        user_id: str,
        new_plan: str,
        supabase: Any,
    ) -> Dict[str, Any]:
        """
        Upgrade subscription to a higher tier.
        Prorated billing should be handled by payment provider.
        """
        # Migrate old plan key if necessary
        new_plan = SubscriptionEngine.migrate_plan_key(new_plan)
        
        try:
            # Get current plan
            resp = (
                supabase.table("profiles")
                .select("subscription_tier")
                .eq("id", user_id)
                .execute()
            )
            
            if not resp.data:
                raise Exception("User profile not found")
            
            current_plan = resp.data[0].get("subscription_tier", Plan.FREE.value)
            
            # Check if actually upgrading
            plan_order = [Plan.FREE.value, Plan.STARTER.value, Plan.PRO.value, Plan.ENTERPRISE.value]
            if plan_order.index(new_plan) <= plan_order.index(current_plan):
                raise Exception("New plan must be higher than current plan")
            
            # Activate new subscription
            result = await BillingLifecycle.activate_subscription(
                user_id,
                new_plan,
                supabase,
            )
            
            logger.info(f"Subscription upgraded for user {user_id}: {current_plan} -> {new_plan}")
            
            return {
                "status": "upgraded",
                "previous_plan": current_plan,
                "new_plan": new_plan,
                **result,
            }
        except Exception as e:
            logger.error(f"Failed to upgrade subscription for user {user_id}: {e}")
            raise
    
    @staticmethod
    async def downgrade_subscription(
        user_id: str,
        new_plan: str,
        supabase: Any,
    ) -> Dict[str, Any]:
        """
        Downgrade subscription to a lower tier.
        Takes effect at next billing date.
        """
        # Migrate old plan key if necessary
        new_plan = SubscriptionEngine.migrate_plan_key(new_plan)
        
        try:
            # Get current plan
            resp = (
                supabase.table("profiles")
                .select("subscription_tier")
                .eq("id", user_id)
                .execute()
            )
            
            if not resp.data:
                raise Exception("User profile not found")
            
            current_plan = resp.data[0].get("subscription_tier", Plan.FREE.value)
            
            # Check if actually downgrading
            plan_order = [Plan.FREE.value, Plan.STARTER.value, Plan.PRO.value, Plan.ENTERPRISE.value]
            if plan_order.index(new_plan) >= plan_order.index(current_plan):
                raise Exception("New plan must be lower than current plan")
            
            # Schedule downgrade at next billing date
            resp = (
                supabase.table("profiles")
                .update({
                    "pending_downgrade_tier": new_plan,
                })
                .eq("id", user_id)
                .execute()
            )
            
            logger.info(f"Downgrade scheduled for user {user_id}: {current_plan} -> {new_plan} at next billing")
            
            return {
                "status": "downgrade_scheduled",
                "current_plan": current_plan,
                "pending_plan": new_plan,
                "effective_at": "next_billing_date",
            }
        except Exception as e:
            logger.error(f"Failed to schedule downgrade for user {user_id}: {e}")
            raise
