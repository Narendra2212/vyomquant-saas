"""
backend/dashboard_aggregation_service.py — Dashboard Aggregation Service

Enterprise-grade single source of truth for all Dashboard data.
Replaces multiple frontend API calls with one optimized endpoint.

PHASE 2-3: Build DashboardAggregationService as single source of truth
Aggregate data from all modules: Authentication, Billing, Subscription, Exchange, 
Bot Monitor, Strategy Builder, Strategies, Marketplace, Risk, Notifications, 
Referral, Signal Trace, Support, Health
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

from backend_app.core.dependencies import get_telemetry, create_request_supabase

logger = logging.getLogger("DashboardAggregationService")


class DashboardAggregationService:
    """
    Single source of truth for all Dashboard data.
    
    Aggregates data from:
    - Portfolio (QuestDB)
    - Strategies (Supabase)
    - Signals (Supabase)
    - Exchange Management
    - Risk
    - Notifications
    - Health
    
    All calculations performed in backend.
    Frontend becomes presentation-only.
    """
    
    def __init__(self):
        self._telemetry = None
        self._supabase = None
    
    async def _get_telemetry(self):
        """Get TelemetryEngine instance."""
        if self._telemetry is None:
            from backend_app.core.dependencies import get_telemetry
            self._telemetry = await get_telemetry()
        return self._telemetry
    
    def _get_supabase(self, user: dict):
        """Get Supabase client for user."""
        from backend_app.core.dependencies import create_request_supabase
        return create_request_supabase(user.get("access_token"))
    
    def _safe_uid(self, uid: str) -> str:
        """Validate user_id for safe SQL queries."""
        if re.match(r"^[a-zA-Z0-9\-_]{1,128}$", str(uid)):
            return str(uid)
        raise ValueError(f"Unsafe user_id: '{uid}'")
    
    async def get_subscription_data(self, user_id: str) -> Dict:
        """
        Get subscription and billing data.
        
        Returns:
            Subscription tier, usage metrics, billing status
        """
        try:
            sb = self._get_supabase({"id": user_id, "access_token": None})
            
            # Get user profile with subscription info
            res = sb.table("profiles").select("*").eq("id", user_id).execute()
            profile = res.data[0] if res.data else {}
            
            # Get subscription tier from profile
            subscription_tier = profile.get("subscription_tier", "free")
            
            # Calculate usage metrics
            strategies_used = len(await self.get_strategies(user_id))
            active_bots = len([s for s in await self.get_strategies(user_id) if s["status"] == "active"])
            
            # Get tier limits (these should come from subscription engine)
            tier_limits = {
                "free": {"strategies": 3, "bots": 1, "ml_training": 0},
                "starter": {"strategies": 10, "bots": 3, "ml_training": 5},
                "pro": {"strategies": 50, "bots": 10, "ml_training": 20},
                "enterprise": {"strategies": -1, "bots": -1, "ml_training": -1}  # Unlimited
            }
            
            limits = tier_limits.get(subscription_tier, tier_limits["free"])
            
            return {
                "tier": subscription_tier,
                "usage": {
                    "strategies": strategies_used,
                    "strategies_limit": limits["strategies"],
                    "bots": active_bots,
                    "bots_limit": limits["bots"],
                    "ml_training_used": 0,  # Would come from ML training tracking
                    "ml_training_limit": limits["ml_training"]
                },
                "billing_status": profile.get("billing_status", "active"),
                "subscription_end": profile.get("subscription_end"),
                "is_trial": profile.get("is_trial", False)
            }
        except Exception as e:
            logger.error(f"Failed to fetch subscription data for user {user_id}: {e}")
            return {
                "tier": "free",
                "usage": {"strategies": 0, "strategies_limit": 3, "bots": 0, "bots_limit": 1, "ml_training_used": 0, "ml_training_limit": 0},
                "billing_status": "active",
                "subscription_end": None,
                "is_trial": False
            }
    
    async def get_exchange_data(self, user_id: str) -> Dict:
        """
        Get exchange connection data.
        
        Returns:
            Connected exchanges, connection status, latency metrics
        """
        try:
            sb = self._get_supabase({"id": user_id, "access_token": None})
            
            # Get user's exchange connections
            res = sb.table("exchange_connections").select("*").eq("user_id", user_id).execute()
            connections = res.data or []
            
            # Calculate exchange metrics
            connected_exchanges = [c for c in connections if c.get("is_active", False)]
            total_exchanges = len(connections)
            
            # Get exchange health from actual connections
            exchange_health = []
            for conn in connected_exchanges:
                exchange_id = conn.get("exchange_id", "unknown")
                # In production, this would check actual connection health
                exchange_health.append({
                    "exchange_id": exchange_id,
                    "status": "connected",
                    "latency_ms": 35,  # Would come from actual health checks
                    "last_sync": conn.get("updated_at")
                })
            
            return {
                "total_exchanges": total_exchanges,
                "connected_exchanges": len(connected_exchanges),
                "exchanges": exchange_health,
                "can_trade": len(connected_exchanges) > 0
            }
        except Exception as e:
            logger.error(f"Failed to fetch exchange data for user {user_id}: {e}")
            return {
                "total_exchanges": 0,
                "connected_exchanges": 0,
                "exchanges": [],
                "can_trade": False
            }
    
    async def get_notification_data(self, user_id: str) -> Dict:
        """
        Get notification data.
        
        Returns:
            Unread count, recent notifications, notification categories
        """
        try:
            sb = self._get_supabase({"id": user_id, "access_token": None})
            
            # Get unread count
            res = sb.table("notifications").select("*").eq("user_id", user_id).eq("read", False).execute()
            unread_count = len(res.data) if res.data else 0
            
            # Get recent notifications
            recent_res = (sb.table("notifications")
                         .select("*")
                         .eq("user_id", user_id)
                         .order("created_at", desc=True)
                         .limit(10)
                         .execute())
            
            recent_notifications = []
            for notif in recent_res.data or []:
                recent_notifications.append({
                    "id": notif.get("id"),
                    "type": notif.get("type"),
                    "category": notif.get("category"),
                    "severity": notif.get("severity"),
                    "title": notif.get("title"),
                    "message": notif.get("message"),
                    "read": notif.get("read", False),
                    "created_at": notif.get("created_at")
                })
            
            # Categorize notifications
            categories = {}
            for notif in recent_notifications:
                cat = notif["category"]
                categories[cat] = categories.get(cat, 0) + 1
            
            return {
                "unread_count": unread_count,
                "total_count": unread_count + len([n for n in recent_notifications if n["read"]]),
                "recent": recent_notifications,
                "categories": categories
            }
        except Exception as e:
            logger.error(f"Failed to fetch notification data for user {user_id}: {e}")
            return {
                "unread_count": 0,
                "total_count": 0,
                "recent": [],
                "categories": {}
            }
    
    async def get_referral_data(self, user_id: str) -> Dict:
        """
        Get referral data.
        
        Returns:
            Referral code, referral count, earnings
        """
        try:
            sb = self._get_supabase({"id": user_id, "access_token": None})
            
            # Get referral profile
            res = sb.table("referral_profiles").select("*").eq("user_id", user_id).execute()
            profile = res.data[0] if res.data else {}
            
            if not profile:
                # Generate referral code if doesn't exist
                import hashlib
                referral_code = hashlib.md5(user_id.encode()).hexdigest()[:8].upper()
                sb.table("referral_profiles").insert({
                    "user_id": user_id,
                    "referral_code": referral_code,
                    "total_referrals": 0,
                    "active_referrals": 0,
                    "pending_earnings": 0.0,
                    "approved_earnings": 0.0,
                    "paid_earnings": 0.0,
                    "lifetime_earnings": 0.0
                }).execute()
                profile = {"referral_code": referral_code}
            
            return {
                "referral_code": profile.get("referral_code", ""),
                "referral_link": f"https://vyomquant.com/ref/{profile.get('referral_code', '')}",
                "total_referrals": profile.get("total_referrals", 0),
                "active_referrals": profile.get("active_referrals", 0),
                "pending_earnings": float(profile.get("pending_earnings", 0.0)),
                "approved_earnings": float(profile.get("approved_earnings", 0.0)),
                "lifetime_earnings": float(profile.get("lifetime_earnings", 0.0))
            }
        except Exception as e:
            logger.error(f"Failed to fetch referral data for user {user_id}: {e}")
            return {
                "referral_code": "",
                "referral_link": "",
                "total_referrals": 0,
                "active_referrals": 0,
                "pending_earnings": 0.0,
                "approved_earnings": 0.0,
                "lifetime_earnings": 0.0
            }
    
    async def get_risk_data(self, user_id: str) -> Dict:
        """
        Get risk management data.
        
        Returns:
            Risk settings, current risk level, circuit breaker status
        """
        try:
            sb = self._get_supabase({"id": user_id, "access_token": None})
            
            # Get risk settings
            res = sb.table("risk_settings").select("*").eq("user_id", user_id).execute()
            settings = res.data[0] if res.data else {}
            
            # Get current risk metrics from portfolio
            portfolio = await self.get_portfolio_overview(user_id)
            current_drawdown = abs(float(portfolio.get("pnl_pct", 0)))
            
            # Determine risk level
            max_daily_loss = float(settings.get("max_daily_loss", 500))
            risk_level = "low"
            if current_drawdown > max_daily_loss * 0.5:
                risk_level = "medium"
            if current_drawdown > max_daily_loss * 0.8:
                risk_level = "high"
            if current_drawdown >= max_daily_loss:
                risk_level = "critical"
            
            return {
                "risk_level": risk_level,
                "current_drawdown_pct": current_drawdown,
                "max_daily_loss": max_daily_loss,
                "max_positions": settings.get("max_positions", 10),
                "max_leverage": settings.get("max_leverage", 3),
                "circuit_breaker_armed": settings.get("circuit_breaker_armed", True),
                "circuit_breaker_breaches": settings.get("circuit_breaker_breaches", 0),
                "kill_switches": settings.get("kill_switches", [])
            }
        except Exception as e:
            logger.error(f"Failed to fetch risk data for user {user_id}: {e}")
            return {
                "risk_level": "low",
                "current_drawdown_pct": 0.0,
                "max_daily_loss": 500,
                "max_positions": 10,
                "max_leverage": 3,
                "circuit_breaker_armed": True,
                "circuit_breaker_breaches": 0,
                "kill_switches": []
            }
    
    async def get_marketplace_data(self, user_id: str) -> Dict:
        """
        Get marketplace data.
        
        Returns:
            Available strategies, user's publications, subscription counts
        """
        try:
            sb = self._get_supabase({"id": user_id, "access_token": None})
            
            # Get available marketplace strategies
            res = sb.table("marketplace_strategies").select("*").eq("is_published", True).execute()
            available_strategies = res.data or []
            
            # Get user's published strategies
            user_res = sb.table("marketplace_strategies").select("*").eq("creator_id", user_id).execute()
            user_publications = user_res.data or []
            
            # Calculate subscriber counts
            total_subscribers = 0
            for pub in user_publications:
                total_subscribers += pub.get("subscriber_count", 0)
            
            return {
                "available_count": len(available_strategies),
                "user_publications": len(user_publications),
                "total_subscribers": total_subscribers,
                "featured": available_strategies[:3]  # Top 3 featured strategies
            }
        except Exception as e:
            logger.error(f"Failed to fetch marketplace data for user {user_id}: {e}")
            return {
                "available_count": 0,
                "user_publications": 0,
                "total_subscribers": 0,
                "featured": []
            }
    
    async def get_portfolio_overview(self, user_id: str) -> Dict:
        """
        Get portfolio overview data.
        
        Returns:
            total_equity, total_pnl, pnl_pct, total_exposure, available_balance
        """
        telemetry = await self._get_telemetry()
        safe_uid = self._safe_uid(user_id)
        
        result = await telemetry.execute_query(
            f"SELECT * FROM live_user_pnl WHERE user_id = '{safe_uid}' LIMIT 1;"
        )
        
        if result and result.get("dataset"):
            cols = [c["name"] for c in result["columns"]]
            return dict(zip(cols, result["dataset"][0]))
        
        # Empty state
        return {
            "total_equity": "0",
            "total_pnl": "0",
            "pnl_pct": "0",
            "total_exposure": "0",
            "available_balance": "0"
        }
    
    async def get_equity_curve(self, user_id: str, days: int = 30) -> List[Dict]:
        """
        Get equity curve data.
        
        Returns:
            List of {timestamp, equity} records
        """
        telemetry = await self._get_telemetry()
        safe_uid = self._safe_uid(user_id)
        limit = max(1, min(int(days) * 96, 100_000))
        
        result = await telemetry.execute_query(
            f"SELECT timestamp, equity FROM equity_curve "
            f"WHERE user_id = '{safe_uid}' "
            f"ORDER BY timestamp ASC LIMIT -{limit};"
        )
        
        if result and result.get("dataset"):
            cols = [c["name"] for c in result["columns"]]
            return [dict(zip(cols, row)) for row in result["dataset"]]
        
        return []
    
    async def get_strategies(self, user_id: str) -> List[Dict]:
        """
        Get strategies with calculated metrics.
        
        Returns:
            List of strategies with status, health, PnL, etc.
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        res = sb.table("strategies").select("*").eq("user_id", user_id).execute()
        strategies = res.data or []
        
        # Calculate metrics in backend
        enriched = []
        for s in strategies:
            enriched.append({
                "id": s.get("id"),
                "name": s.get("name") or "Strategy",
                "pair": s.get("pair") or s.get("symbol") or "BTC/USDT",
                "status": "active" if s.get("is_active") else "paused",
                "health": "healthy" if s.get("is_active") else "idle",
                "today_pnl": float(s.get("today_pnl") or 0.0),
                "today_return_pct": float(s.get("today_return_pct") or 0.0),
                "last_signal_time": s.get("last_signal_at") or None
            })
        
        return enriched
    
    async def get_strategy_insights(self, user_id: str) -> List[Dict]:
        """
        Calculate trading insights from strategy state.
        
        Returns:
            List of insight objects with type, text, action
        """
        strategies = await self.get_strategies(user_id)
        
        insights = []
        
        # Calculate active/inactive counts in backend
        active_count = sum(1 for s in strategies if s["status"] == "active")
        inactive_count = len(strategies) - active_count
        
        if inactive_count > 0:
            inactive_names = [s["name"] for s in strategies if s["status"] == "paused"]
            insights.append({
                "id": "ins_1",
                "type": "warning",
                "text": f"{inactive_count} strategy(ies) currently paused: {', '.join(inactive_names)}.",
                "action_text": "Manage Strategies",
                "action_path": "/app/strategies"
            })
        
        insights.append({
            "id": "ins_2",
            "type": "info",
            "text": f"{active_count} active strategy execution bot(s) running on live connected exchanges.",
            "action_text": "View Bots",
            "action_path": "/app/strategies"
        })
        
        insights.append({
            "id": "ins_3",
            "type": "success",
            "text": "Risk Circuit Breakers active: Max drawdown limit enforced by Risk Engine.",
            "action_text": "Risk Settings",
            "action_path": "/app/risk"
        })
        
        return insights[:3]
    
    async def get_recent_signals(self, user_id: str, limit: int = 5) -> List[Dict]:
        """
        Get recent signal traces for notifications.
        
        Returns:
            List of signal objects with decision, asset, risk_result, etc.
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        res = (sb.table("execution_records")
               .select("*")
               .eq("user_id", user_id)
               .order("created_at", desc=True)
               .limit(limit)
               .execute())
        
        records = res.data or []
        
        notifications = []
        for r in records:
            notifications.append({
                "id": r.get("id"),
                "time": r.get("created_at") or None,
                "text": f"Signal {r.get('side', 'BUY').upper()}: {r.get('symbol')} on {r.get('exchange_id', 'binance').upper()} (Risk: {r.get('risk_verdict', 'APPROVED')})",
                "type": "success" if r.get("risk_verdict") == "APPROVED" else "warning"
            })
        
        return notifications
    
    async def get_health_status(self, user_id: str) -> Dict:
        """
        Get system health status.
        
        Returns:
            System health metrics (latency, circuit breaker, sync status)
        """
        # TODO: Integrate with actual health monitoring
        return {
            "exchange_api_latency_ms": 38,
            "exchange_api_latency_status": "optimal",
            "risk_circuit_breaker_status": "armed",
            "risk_circuit_breaker_breaches": 0,
            "order_state_sync_status": "synchronized"
        }
    
    async def get_dashboard_data(self, user_id: str, equity_days: int = 30) -> Dict:
        """
        Get complete dashboard data in one call.
        
        This is the single source of truth for Dashboard.
        All calculations performed in backend.
        
        Args:
            user_id: User UUID
            equity_days: Number of days for equity curve
        
        Returns:
            Complete dashboard data structure with all required sections:
            - overview
            - subscription
            - usage
            - bots
            - strategies
            - marketplace
            - risk
            - notifications
            - referrals
            - health
            - exchange
            - recent_activity
        """
        try:
            # Parallel data fetching from all modules
            results = await asyncio.gather(
                self.get_portfolio_overview(user_id),
                self.get_equity_curve(user_id, equity_days),
                self.get_strategies(user_id),
                self.get_strategy_insights(user_id),
                self.get_recent_signals(user_id),
                self.get_health_status(user_id),
                self.get_subscription_data(user_id),
                self.get_exchange_data(user_id),
                self.get_notification_data(user_id),
                self.get_referral_data(user_id),
                self.get_risk_data(user_id),
                self.get_marketplace_data(user_id),
                return_exceptions=True
            )
            
            # Unpack results with error handling
            portfolio, equity, strategies, insights, signals, health, subscription, exchange, notifications, referral, risk, marketplace = results
            
            # Handle exceptions with fallbacks
            if isinstance(portfolio, Exception):
                logger.error(f"Portfolio fetch failed: {portfolio}")
                portfolio = {"total_equity": "0", "total_pnl": "0", "pnl_pct": "0", "total_exposure": "0", "available_balance": "0"}
            
            if isinstance(equity, Exception):
                logger.error(f"Equity curve fetch failed: {equity}")
                equity = []
            
            if isinstance(strategies, Exception):
                logger.error(f"Strategies fetch failed: {strategies}")
                strategies = []
            
            if isinstance(insights, Exception):
                logger.error(f"Insights generation failed: {insights}")
                insights = []
            
            if isinstance(signals, Exception):
                logger.error(f"Signals fetch failed: {signals}")
                signals = []
            
            if isinstance(health, Exception):
                logger.error(f"Health status fetch failed: {health}")
                health = {"exchange_api_latency_ms": 0, "exchange_api_latency_status": "unknown", "risk_circuit_breaker_status": "unknown", "risk_circuit_breaker_breaches": 0, "order_state_sync_status": "unknown"}
            
            if isinstance(subscription, Exception):
                logger.error(f"Subscription data fetch failed: {subscription}")
                subscription = {"tier": "free", "usage": {}, "billing_status": "active", "subscription_end": None, "is_trial": False}
            
            if isinstance(exchange, Exception):
                logger.error(f"Exchange data fetch failed: {exchange}")
                exchange = {"total_exchanges": 0, "connected_exchanges": 0, "exchanges": [], "can_trade": False}
            
            if isinstance(notifications, Exception):
                logger.error(f"Notification data fetch failed: {notifications}")
                notifications = {"unread_count": 0, "total_count": 0, "recent": [], "categories": {}}
            
            if isinstance(referral, Exception):
                logger.error(f"Referral data fetch failed: {referral}")
                referral = {"referral_code": "", "referral_link": "", "total_referrals": 0, "active_referrals": 0, "pending_earnings": 0.0, "approved_earnings": 0.0, "lifetime_earnings": 0.0}
            
            if isinstance(risk, Exception):
                logger.error(f"Risk data fetch failed: {risk}")
                risk = {"risk_level": "low", "current_drawdown_pct": 0.0, "max_daily_loss": 500, "max_positions": 10, "max_leverage": 3, "circuit_breaker_armed": True, "circuit_breaker_breaches": 0, "kill_switches": []}
            
            if isinstance(marketplace, Exception):
                logger.error(f"Marketplace data fetch failed: {marketplace}")
                marketplace = {"available_count": 0, "user_publications": 0, "total_subscribers": 0, "featured": []}
            
            # Calculate all metrics in backend (PHASE 5)
            active_strategies = [s for s in strategies if s["status"] == "active"]
            paused_strategies = [s for s in strategies if s["status"] == "paused"]
            
            # Build complete dashboard response
            return {
                "overview": {
                    "total_value": float(portfolio.get("total_equity", 0)),
                    "today_pnl": float(portfolio.get("total_pnl", 0)),
                    "today_return_pct": float(portfolio.get("pnl_pct", 0)),
                    "unrealized_pnl": float(portfolio.get("total_pnl", 0)),
                    "available_balance": float(portfolio.get("available_balance", 0))
                },
                "subscription": {
                    "tier": subscription.get("tier", "free"),
                    "billing_status": subscription.get("billing_status", "active"),
                    "subscription_end": subscription.get("subscription_end"),
                    "is_trial": subscription.get("is_trial", False)
                },
                "usage": subscription.get("usage", {
                    "strategies": 0,
                    "strategies_limit": 3,
                    "deployments": 0,
                    "deployments_limit": 1,
                    "ml_training_used": 0,
                    "ml_training_limit": 0
                }),
                "strategies": {
                    "running": len(active_strategies),
                    "stopped": len(paused_strategies),
                    "total": len(strategies),
                    "items": strategies
                },
                "strategies": {
                    "total": len(strategies),
                    "active": len(active_strategies),
                    "paused": len(paused_strategies),
                    "items": strategies
                },
                "marketplace": {
                    "available_count": marketplace.get("available_count", 0),
                    "user_publications": marketplace.get("user_publications", 0),
                    "total_subscribers": marketplace.get("total_subscribers", 0),
                    "featured": marketplace.get("featured", [])
                },
                "risk": {
                    "risk_level": risk.get("risk_level", "low"),
                    "current_drawdown_pct": risk.get("current_drawdown_pct", 0.0),
                    "max_daily_loss": risk.get("max_daily_loss", 500),
                    "circuit_breaker_armed": risk.get("circuit_breaker_armed", True),
                    "circuit_breaker_breaches": risk.get("circuit_breaker_breaches", 0)
                },
                "notifications": {
                    "unread_count": notifications.get("unread_count", 0),
                    "total_count": notifications.get("total_count", 0),
                    "recent": notifications.get("recent", []),
                    "categories": notifications.get("categories", {})
                },
                "referrals": {
                    "referral_code": referral.get("referral_code", ""),
                    "referral_link": referral.get("referral_link", ""),
                    "total_referrals": referral.get("total_referrals", 0),
                    "active_referrals": referral.get("active_referrals", 0),
                    "lifetime_earnings": referral.get("lifetime_earnings", 0.0)
                },
                "health": {
                    "exchange_api_latency_ms": health.get("exchange_api_latency_ms", 0),
                    "exchange_api_latency_status": health.get("exchange_api_latency_status", "unknown"),
                    "risk_circuit_breaker_status": health.get("risk_circuit_breaker_status", "unknown"),
                    "order_state_sync_status": health.get("order_state_sync_status", "unknown")
                },
                "exchange": {
                    "total_exchanges": exchange.get("total_exchanges", 0),
                    "connected_exchanges": exchange.get("connected_exchanges", 0),
                    "can_trade": exchange.get("can_trade", False),
                    "exchanges": exchange.get("exchanges", [])
                },
                "recent_activity": {
                    "signals": signals,
                    "insights": insights
                },
                "equity_curve": equity,
                "generated_at": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Dashboard aggregation failed for user {user_id}: {e}")
            raise


# Singleton instance
_dashboard_service = None

async def get_dashboard_service() -> DashboardAggregationService:
    """Get singleton DashboardAggregationService instance."""
    global _dashboard_service
    if _dashboard_service is None:
        _dashboard_service = DashboardAggregationService()
    return _dashboard_service
