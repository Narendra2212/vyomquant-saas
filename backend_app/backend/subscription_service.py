"""
backend/subscription_service.py — Subscription Service

PHASE 10: Marketplace Subscription Workflow

When user subscribes to a marketplace strategy:
- Strategy automatically appears in Strategies
- Distinguish: Owned, Subscribed, Published, Template, Read Only, Protected
- Users may: Configure Parameters, Select Exchange, Deploy, Backtest, Clone (if allowed)
- Never modify protected logic

Provides:
- Subscribe to marketplace strategies
- Unsubscribe from strategies
- List user subscriptions
- Get subscription details
- Configure subscribed strategy parameters
- Clone subscribed strategy (if allowed)
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from backend_app.core.dependencies import create_request_supabase

logger = logging.getLogger("SubscriptionService")


class SubscriptionService:
    """
    Central service for marketplace subscription operations.
    
    Manages user subscriptions to marketplace strategies.
    """
    
    def __init__(self):
        pass
    
    def _get_supabase(self, user: dict):
        """Get Supabase client for user."""
        return create_request_supabase(user.get("access_token"))
    
    async def subscribe_to_strategy(
        self,
        user_id: str,
        marketplace_listing_id: str,
        configuration: Optional[Dict] = None
    ) -> Dict:
        """
        Subscribe to a marketplace strategy.
        
        Creates a read-only copy of the strategy in user's strategies.
        
        Args:
            user_id: User ID
            marketplace_listing_id: Marketplace listing ID
            configuration: User-specific configuration (exchange, parameters, etc.)
            
        Returns:
            Subscription record and strategy
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        # Get marketplace listing
        listing_res = sb.table("marketplace_listings").select("*").eq("id", marketplace_listing_id).execute()
        if not listing_res.data:
            raise ValueError(f"Marketplace listing {marketplace_listing_id} not found")
        
        listing = listing_res.data[0]
        
        # Check if already subscribed
        existing_sub = (sb.table("strategy_subscriptions")
                      .select("*")
                      .eq("user_id", user_id)
                      .eq("marketplace_listing_id", marketplace_listing_id)
                      .execute())
        
        if existing_sub.data:
            raise ValueError("Already subscribed to this strategy")
        
        # Get original strategy
        original_strategy_res = sb.table("strategies").select("*").eq("id", listing["strategy_id"]).execute()
        if not original_strategy_res.data:
            raise ValueError("Original strategy not found")
        
        original_strategy = original_strategy_res.data[0]
        
        # Get original version
        original_version_res = (sb.table("strategy_versions")
                              .select("*")
                              .eq("strategy_id", listing["strategy_id"])
                              .eq("version", listing["version"])
                              .execute())
        
        if not original_version_res.data:
            raise ValueError("Original version not found")
        
        original_version = original_version_res.data[0]
        
        # Create subscription record
        subscription_id = str(uuid4())
        subscription_data = {
            "id": subscription_id,
            "user_id": user_id,
            "marketplace_listing_id": marketplace_listing_id,
            "original_strategy_id": listing["strategy_id"],
            "original_version_id": original_version["id"],
            "original_version": listing["version"],
            "configuration": configuration or {},
            "status": "active",
            "subscribed_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        subscription_result = sb.table("strategy_subscriptions").insert(subscription_data).execute()
        
        # Create read-only strategy copy for user
        user_strategy_id = str(uuid4())
        user_strategy_data = {
            "id": user_strategy_id,
            "user_id": user_id,
            "name": f"{listing['title']} (Subscribed)",
            "description": listing["description"],
            "exchange": configuration.get("exchange", original_strategy["exchange"]) if configuration else original_strategy["exchange"],
            "symbol": original_strategy["symbol"],
            "timeframe": original_strategy["timeframe"],
            "tags": listing.get("tags", []),
            "status": "draft",
            "environment": "paper",
            "current_version": listing["version"],
            "is_subscribed": True,
            "is_read_only": True,
            "subscription_id": subscription_id,
            "original_strategy_id": listing["strategy_id"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        strategy_result = sb.table("strategies").insert(user_strategy_data).execute()
        
        # Create version copy
        user_version_id = str(uuid4())
        user_version_data = {
            "id": user_version_id,
            "strategy_id": user_strategy_id,
            "version": listing["version"],
            "blueprint": original_version["blueprint"],
            "is_draft": True,
            "is_current": True,
            "is_read_only": True,
            "original_version_id": original_version["id"],
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        version_result = sb.table("strategy_versions").insert(user_version_data).execute()
        
        logger.info(f"User {user_id} subscribed to marketplace listing {marketplace_listing_id}")
        
        return {
            "subscription": subscription_result.data[0] if subscription_result.data else subscription_data,
            "strategy": strategy_result.data[0] if strategy_result.data else user_strategy_data,
            "version": version_result.data[0] if version_result.data else user_version_data
        }
    
    async def unsubscribe_from_strategy(
        self,
        user_id: str,
        subscription_id: str
    ) -> bool:
        """
        Unsubscribe from a marketplace strategy.
        
        Args:
            user_id: User ID
            subscription_id: Subscription ID
            
        Returns:
            Success status
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        # Get subscription
        sub_res = sb.table("strategy_subscriptions").select("*").eq("id", subscription_id).eq("user_id", user_id).execute()
        if not sub_res.data:
            raise ValueError("Subscription not found")
        
        subscription = sub_res.data[0]
        
        # Stop any running deployments
        from backend_app.backend.strategy_service import get_strategy_service
        strategy_service = await get_strategy_service()
        
        # Find user's subscribed strategy
        strategy_res = (sb.table("strategies")
                       .select("id")
                       .eq("subscription_id", subscription_id)
                       .eq("user_id", user_id)
                       .execute())
        
        if strategy_res.data:
            user_strategy_id = strategy_res.data[0]["id"]
            await strategy_service.stop_all_deployments(user_id, user_strategy_id)
            
            # Delete user's strategy copy
            sb.table("strategies").delete().eq("id", user_strategy_id).execute()
        
        # Delete subscription
        sb.table("strategy_subscriptions").delete().eq("id", subscription_id).execute()
        
        logger.info(f"User {user_id} unsubscribed from subscription {subscription_id}")
        
        return True
    
    async def list_subscriptions(
        self,
        user_id: str
    ) -> List[Dict]:
        """
        List all user's marketplace subscriptions.
        
        Args:
            user_id: User ID
            
        Returns:
            List of subscriptions with strategy details
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        # Get subscriptions
        sub_res = (sb.table("strategy_subscriptions")
                  .select("*")
                  .eq("user_id", user_id)
                  .order("subscribed_at", desc=True)
                  .execute())
        
        subscriptions = sub_res.data or []
        
        # Enrich with marketplace listing details
        enriched = []
        for sub in subscriptions:
            listing_res = sb.table("marketplace_listings").select("*").eq("id", sub["marketplace_listing_id"]).execute()
            if listing_res.data:
                enriched.append({
                    **sub,
                    "listing": listing_res.data[0]
                })
        
        return enriched
    
    async def get_subscription(
        self,
        user_id: str,
        subscription_id: str
    ) -> Optional[Dict]:
        """
        Get subscription details.
        
        Args:
            user_id: User ID
            subscription_id: Subscription ID
            
        Returns:
            Subscription with marketplace listing details
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        sub_res = sb.table("strategy_subscriptions").select("*").eq("id", subscription_id).eq("user_id", user_id).execute()
        if not sub_res.data:
            return None
        
        subscription = sub_res.data[0]
        
        # Get marketplace listing
        listing_res = sb.table("marketplace_listings").select("*").eq("id", subscription["marketplace_listing_id"]).execute()
        
        return {
            "subscription": subscription,
            "listing": listing_res.data[0] if listing_res.data else {}
        }
    
    async def update_subscription_configuration(
        self,
        user_id: str,
        subscription_id: str,
        configuration: Dict
    ) -> Dict:
        """
        Update subscription configuration.
        
        Users can configure parameters and exchange settings
        without modifying the protected strategy logic.
        
        Args:
            user_id: User ID
            subscription_id: Subscription ID
            configuration: Updated configuration
            
        Returns:
            Updated subscription
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        update_data = {
            "configuration": configuration,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        result = sb.table("strategy_subscriptions").update(update_data).eq("id", subscription_id).eq("user_id", user_id).execute()
        
        # Update user's strategy copy if exchange changed
        if "exchange" in configuration:
            strategy_res = (sb.table("strategies")
                           .select("id")
                           .eq("subscription_id", subscription_id)
                           .eq("user_id", user_id)
                           .execute())
            
            if strategy_res.data:
                sb.table("strategies").update({
                    "exchange": configuration["exchange"],
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }).eq("id", strategy_res.data[0]["id"]).execute()
        
        logger.info(f"Updated configuration for subscription {subscription_id}")
        
        return {
            "subscription": result.data[0] if result.data else {}
        }
    
    async def clone_subscribed_strategy(
        self,
        user_id: str,
        subscription_id: str,
        new_name: str
    ) -> Dict:
        """
        Clone a subscribed strategy (if publisher allows).
        
        Creates a new owned strategy based on the subscribed one.
        Check publisher's clone_allowed flag first.
        
        Args:
            user_id: User ID
            subscription_id: Subscription ID
            new_name: Name for cloned strategy
            
        Returns:
            New strategy record
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        # Get subscription
        sub_res = sb.table("strategy_subscriptions").select("*").eq("id", subscription_id).eq("user_id", user_id).execute()
        if not sub_res.data:
            raise ValueError("Subscription not found")
        
        subscription = sub_res.data[0]
        
        # Get marketplace listing to check clone permission
        listing_res = sb.table("marketplace_listings").select("*").eq("id", subscription["marketplace_listing_id"]).execute()
        if not listing_res.data:
            raise ValueError("Marketplace listing not found")
        
        listing = listing_res.data[0]
        
        # Check if clone is allowed
        if not listing.get("allow_clone", False):
            raise ValueError("Publisher does not allow cloning this strategy")
        
        # Get user's subscribed strategy
        user_strategy_res = (sb.table("strategies")
                            .select("*")
                            .eq("subscription_id", subscription_id)
                            .eq("user_id", user_id)
                            .execute())
        
        if not user_strategy_res.data:
            raise ValueError("User strategy not found")
        
        user_strategy = user_strategy_res.data[0]
        
        # Clone using StrategyService
        from backend_app.backend.strategy_service import get_strategy_service
        strategy_service = await get_strategy_service()
        
        # Get current version
        version_res = (sb.table("strategy_versions")
                      .select("*")
                      .eq("strategy_id", user_strategy["id"])
                      .eq("is_current", True)
                      .execute())
        
        if not version_res.data:
            raise ValueError("Current version not found")
        
        version = version_res.data[0]
        
        # Create new owned strategy
        new_strategy_id = str(uuid4())
        new_strategy_data = {
            "id": new_strategy_id,
            "user_id": user_id,
            "name": new_name,
            "description": f"Cloned from {user_strategy['name']}",
            "exchange": user_strategy["exchange"],
            "symbol": user_strategy["symbol"],
            "timeframe": user_strategy["timeframe"],
            "tags": user_strategy.get("tags", []),
            "status": "draft",
            "environment": "paper",
            "current_version": "v1.0",
            "is_subscribed": False,
            "is_read_only": False,
            "cloned_from": user_strategy["id"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        strategy_result = sb.table("strategies").insert(new_strategy_data).execute()
        
        # Create version with cloned blueprint
        new_version_id = str(uuid4())
        new_version_data = {
            "id": new_version_id,
            "strategy_id": new_strategy_id,
            "version": "v1.0",
            "blueprint": version["blueprint"],
            "is_draft": True,
            "is_current": True,
            "is_read_only": False,
            "cloned_from_version": version["id"],
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        version_result = sb.table("strategy_versions").insert(new_version_data).execute()
        
        logger.info(f"Cloned subscribed strategy {subscription_id} as {new_strategy_id}")
        
        return {
            "strategy": strategy_result.data[0] if strategy_result.data else new_strategy_data,
            "version": version_result.data[0] if version_result.data else new_version_data
        }


# Singleton instance
_subscription_service = None

async def get_subscription_service() -> SubscriptionService:
    """Get singleton SubscriptionService instance."""
    global _subscription_service
    if _subscription_service is None:
        _subscription_service = SubscriptionService()
    return _subscription_service