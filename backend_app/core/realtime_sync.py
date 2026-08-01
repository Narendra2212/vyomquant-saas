"""
core/realtime_sync.py — Realtime Synchronization Service

Handles automatic realtime synchronization of subscription changes:
Webhook → Database → Redis → WebSocket → Frontend

This ensures that subscription changes are immediately reflected across all systems.
"""

import json
import logging
from typing import Any, Dict

from backend_app.api_ws.ws_manager import manager as ws_manager
from backend_app.core.cache import redis_manager

logger = logging.getLogger("RealtimeSync")


class RealtimeSync:
    """Realtime synchronization service."""
    
    @staticmethod
    async def broadcast_subscription_change(
        user_id: str,
        event_type: str,
        data: Dict[str, Any],
    ):
        """
        Broadcast subscription change to user via WebSocket.
        
        Args:
            user_id: User ID to broadcast to
            event_type: Type of event (e.g., 'subscription_upgraded', 'quota_reached')
            data: Event data payload
        """
        try:
            message = {
                "type": "subscription_update",
                "event": event_type,
                "data": data,
                "timestamp": json.dumps({"value": "now"}),  # Placeholder for timestamp
            }
            
            await ws_manager.broadcast_user(user_id, message)
            logger.info(f"Broadcasted subscription update to user {user_id}: {event_type}")
        except Exception as e:
            logger.error(f"Failed to broadcast subscription update: {e}")
    
    @staticmethod
    async def invalidate_subscription_cache(user_id: str):
        """
        Invalidate all subscription-related cache entries for a user.
        
        This ensures that the next request fetches fresh data from the database.
        """
        try:
            # Invalidate profile cache
            await redis_manager.delete(f"profile_limits:{user_id}")
            
            # Invalidate frozen status cache
            await redis_manager.delete(f"subscription:frozen:{user_id}")
            
            # Invalidate subscription status cache
            await redis_manager.delete(f"subscription:status:{user_id}")
            
            # Invalidate quota caches
            await redis_manager.delete(f"quota:{user_id}:strategies")
            await redis_manager.delete(f"quota:{user_id}:bots")
            await redis_manager.delete(f"quota:{user_id}:ml_trainings")
            await redis_manager.delete(f"quota:{user_id}:marketplace_published")
            
            logger.info(f"Invalidated subscription cache for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to invalidate subscription cache: {e}")
    
    @staticmethod
    async def sync_subscription_change(
        user_id: str,
        event_type: str,
        data: Dict[str, Any],
    ):
        """
        Full sync: invalidate cache and broadcast to WebSocket.
        
        This is called after webhook processing to ensure all systems are in sync.
        """
        try:
            # Step 1: Invalidate cache
            await RealtimeSync.invalidate_subscription_cache(user_id)
            
            # Step 2: Broadcast to WebSocket
            await RealtimeSync.broadcast_subscription_change(user_id, event_type, data)
            
            logger.info(f"Full subscription sync completed for user {user_id}: {event_type}")
        except Exception as e:
            logger.error(f"Failed to sync subscription change: {e}")
    
    @staticmethod
    async def notify_quota_reached(
        user_id: str,
        resource: str,
        current_usage: int,
        limit: int,
    ):
        """
        Notify user when quota limit is reached.
        """
        try:
            message = {
                "type": "quota_warning",
                "resource": resource,
                "current_usage": current_usage,
                "limit": limit,
                "message": f"You have reached your {resource} limit ({current_usage}/{limit}). Upgrade your plan to continue.",
            }
            
            await ws_manager.broadcast_user(user_id, message)
            logger.warning(f"Quota reached notification sent to user {user_id}: {resource} {current_usage}/{limit}")
        except Exception as e:
            logger.error(f"Failed to send quota reached notification: {e}")
    
    @staticmethod
    async def notify_quota_exceeded(
        user_id: str,
        resource: str,
        current_usage: int,
        limit: int,
    ):
        """
        Notify user when quota is exceeded and action was blocked.
        """
        try:
            message = {
                "type": "quota_exceeded",
                "resource": resource,
                "current_usage": current_usage,
                "limit": limit,
                "message": f"Action blocked: {resource} quota exceeded ({current_usage}/{limit}). Upgrade your plan to continue.",
            }
            
            await ws_manager.broadcast_user(user_id, message)
            logger.warning(f"Quota exceeded notification sent to user {user_id}: {resource} {current_usage}/{limit}")
        except Exception as e:
            logger.error(f"Failed to send quota exceeded notification: {e}")
    
    @staticmethod
    async def notify_payment_failed(user_id: str, payment_id: str):
        """
        Notify user of payment failure.
        """
        try:
            message = {
                "type": "payment_failed",
                "payment_id": payment_id,
                "message": "Payment failed. Please update your payment method to avoid service interruption.",
            }
            
            await ws_manager.broadcast_user(user_id, message)
            logger.warning(f"Payment failed notification sent to user {user_id}")
        except Exception as e:
            logger.error(f"Failed to send payment failed notification: {e}")
    
    @staticmethod
    async def notify_subscription_expiring(user_id: str, days_remaining: int):
        """
        Notify user that subscription is expiring soon.
        """
        try:
            message = {
                "type": "subscription_expiring",
                "days_remaining": days_remaining,
                "message": f"Your subscription expires in {days_remaining} days. Renew to avoid service interruption.",
            }
            
            await ws_manager.broadcast_user(user_id, message)
            logger.info(f"Subscription expiring notification sent to user {user_id}: {days_remaining} days")
        except Exception as e:
            logger.error(f"Failed to send subscription expiring notification: {e}")
    
    @staticmethod
    async def notify_plan_changed(
        user_id: str,
        previous_plan: str,
        new_plan: str,
    ):
        """
        Notify user of plan change.
        """
        try:
            message = {
                "type": "plan_changed",
                "previous_plan": previous_plan,
                "new_plan": new_plan,
                "message": f"Your plan has been changed from {previous_plan} to {new_plan}.",
            }
            
            await ws_manager.broadcast_user(user_id, message)
            logger.info(f"Plan changed notification sent to user {user_id}: {previous_plan} -> {new_plan}")
        except Exception as e:
            logger.error(f"Failed to send plan changed notification: {e}")
