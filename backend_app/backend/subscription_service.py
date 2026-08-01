"""
backend/subscription_service.py — Subscription Service

DEPRECATED: Subscription operations moved to library.py router
Use /api/library/* endpoints for subscriptions.

This file is kept for backward compatibility only.
"""

import logging

logger = logging.getLogger("SubscriptionService")


class SubscriptionService:
    """
    DEPRECATED: Subscription operations moved to library.py
    """
    
    def __init__(self):
        logger.warning("SubscriptionService is deprecated. Use /api/library/* endpoints instead.")
    
    async def subscribe_to_strategy(self, *args, **kwargs):
        raise NotImplementedError("Use POST /api/library/{library_id}/checkout instead")
    
    async def unsubscribe_from_strategy(self, *args, **kwargs):
        raise NotImplementedError("Use DELETE /api/library/subscriptions/{subscription_id} instead")
    
    async def list_subscriptions(self, *args, **kwargs):
        raise NotImplementedError("Use GET /api/library/subscriber/analytics instead")
    
    async def get_subscription(self, *args, **kwargs):
        raise NotImplementedError("Use GET /api/library/subscriptions/{subscription_id} instead")
    
    async def update_subscription_configuration(self, *args, **kwargs):
        raise NotImplementedError("Use PUT /api/library/subscriptions/{subscription_id}/configuration instead")
    
    async def clone_subscribed_strategy(self, *args, **kwargs):
        raise NotImplementedError("Use POST /api/library/{library_id}/clone instead")


# Singleton instance
_subscription_service = None

async def get_subscription_service() -> SubscriptionService:
    """Get singleton SubscriptionService instance (deprecated)."""
    global _subscription_service
    if _subscription_service is None:
        _subscription_service = SubscriptionService()
    return _subscription_service