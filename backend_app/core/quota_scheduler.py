"""
core/quota_scheduler.py — Monthly Quota Reset Scheduler

Scheduled task to reset monthly quotas for all users.
Runs on the first day of each month at 00:00 UTC.
"""

import asyncio
import logging
from datetime import datetime

from backend_app.core.cache import redis_manager
from backend_app.core.subscription_engine import Resource, SubscriptionEngine

logger = logging.getLogger("QuotaScheduler")


async def reset_all_monthly_quotas():
    """
    Reset monthly quotas for all users.
    This should be called by a scheduled task (e.g., Celery beat, APScheduler).
    """
    logger.info("Starting monthly quota reset for all users")
    
    # Get all user IDs from Redis quota keys
    # Pattern: quota:{user_id}:{resource}
    # We need to find all unique user_ids that have monthly quota usage
    
    try:
        # Get all quota keys
        quota_keys = await redis_manager.keys("quota:*")
        
        if not quota_keys:
            logger.info("No quota keys found, nothing to reset")
            return
        
        # Extract unique user IDs
        user_ids = set()
        monthly_resources = {Resource.ML_TRAININGS.value, Resource.MARKETPLACE_PUBLISHED.value}
        
        for key in quota_keys:
            parts = key.split(":")
            if len(parts) >= 3:
                resource = parts[2]
                if resource in monthly_resources:
                    user_ids.add(parts[1])
        
        logger.info(f"Found {len(user_ids)} users with monthly quota usage")
        
        # Reset quotas for each user
        reset_count = 0
        for user_id in user_ids:
            try:
                await SubscriptionEngine.reset_monthly_quotas(user_id)
                reset_count += 1
            except Exception as e:
                logger.error(f"Failed to reset quotas for user {user_id}: {e}")
        
        logger.info(f"Monthly quota reset complete: {reset_count}/{len(user_ids)} users")
        
    except Exception as e:
        logger.error(f"Monthly quota reset failed: {e}")


def should_run_monthly_reset() -> bool:
    """
    Check if today is the first day of the month.
    Returns True if the scheduled task should run.
    """
    now = datetime.utcnow()
    return now.day == 1


async def run_if_scheduled():
    """
    Run monthly quota reset if scheduled (first day of month).
    This can be called periodically (e.g., daily) to check and run when needed.
    """
    if should_run_monthly_reset():
        await reset_all_monthly_quotas()
    else:
        logger.debug("Not first day of month, skipping quota reset")
