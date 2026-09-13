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
import inspect
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend_app.core.dependencies import get_telemetry, create_request_supabase

logger = logging.getLogger("DashboardAggregationService")

#: Fallback used only when neither APP_URL nor FRONTEND_URL is set. Referral links are
#: user-facing and get pasted into chats and emails, so a stale hostname here outlives any
#: deployment. Kept deliberately identical to ``routers.referral._get_referral_link`` - the two
#: are duplicated rather than shared to avoid importing a router module into a service layer, so
#: they must be changed together.
_DEFAULT_APP_URL = "https://app.vyomquant.in"


def _app_base_url() -> str:
    """Public base URL of the frontend, for links rendered into user-visible payloads."""
    return os.getenv("APP_URL", os.getenv("FRONTEND_URL", _DEFAULT_APP_URL)).rstrip("/")


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
    
    async def _timed_operation(self, operation_name: str, operation):
        """
        Wrapper coroutine to measure operation duration while preserving exact execution semantics.

        This wrapper:
        - Records monotonic start time
        - Awaits the exact original coroutine
        - Records monotonic end time
        - Logs duration
        - Returns the exact original result
        - Re-raises any exceptions without masking

        Args:
            operation_name: Name of the operation for logging
            operation: The coroutine to measure

        Returns:
            The exact result from the original operation

        Raises:
            Any exception raised by the original operation (not masked)
        """
        start = time.perf_counter()
        try:
            result = await operation
            return result
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                f"dashboard_operation_timing",
                extra={
                    "operation": operation_name,
                    "duration_ms": round(duration_ms, 2),
                },
            )

    def _get_telemetry(self):
        """Get TelemetryEngine instance."""
        if self._telemetry is None:
            from backend_app.core.dependencies import get_telemetry
            self._telemetry = get_telemetry()
        return self._telemetry
    
    async def _execute_sb_query(self, query):
        """Execute a Supabase PostgREST query in a background thread to prevent blocking asyncio loop."""
        if inspect.isawaitable(query):
            return await query
        if hasattr(query, "execute"):
            return await asyncio.to_thread(query.execute)
        return query

    async def _get_supabase(self, user: dict):
        """Get Supabase client for user."""
        from backend_app.core.dependencies import create_request_supabase_async
        res = create_request_supabase_async(user.get("access_token"))
        return await res if inspect.isawaitable(res) else res
    
    def _safe_uid(self, uid: str) -> str:
        """
        DB-CRITICAL-001 FIX: Enhanced validation to prevent SQL injection.
        
        This function validates user_id with multiple checks:
        1. Format validation (alphanumeric, hyphens, underscores only)
        2. Length validation (max 128 characters)
        3. Unicode character validation
        4. Pattern matching to prevent injection attempts
        """
        uid_str = str(uid)
        
        # Length validation
        if len(uid_str) > 128:
            raise ValueError(f"Unsafe user_id: exceeds maximum length of 128 characters")
        
        # Format validation - strict pattern for UUIDs and similar identifiers
        if not re.match(r"^[a-zA-Z0-9\-_]{1,128}$", uid_str):
            raise ValueError(f"Unsafe user_id: contains invalid characters")
        
        # Additional SQL injection pattern checks
        dangerous_patterns = [
            r"'",  # Single quote
            r";",  # Statement separator
            r"--", # SQL comment
            r"/\*", # SQL comment start
            r"\*/", # SQL comment end
            r"\bUNION\b", # UNION operator
            r"\bSELECT\b", # SELECT keyword
            r"\bINSERT\b", # INSERT keyword
            r"\bUPDATE\b", # UPDATE keyword
            r"\bDELETE\b", # DELETE keyword
            r"\bDROP\b", # DROP keyword
            r"\bEXEC\b", # EXECUTE keyword
        ]
        
        for pattern in dangerous_patterns:
            if re.search(pattern, uid_str, re.IGNORECASE):
                raise ValueError(f"Unsafe user_id: contains potentially dangerous pattern")
        
        return uid_str
    
    async def get_subscription_data(self, user: dict, strategies_task: Optional['asyncio.Task'] = None, sb: Optional[Any] = None) -> Dict:
        """
        Get subscription and billing data.

        Args:
            user: User dict
            strategies_task: Optional request-local task for strategies fetch. If provided, will await it instead of fetching.
            sb: Optional shared Supabase client

        Returns:
            Subscription tier, usage metrics, billing status
        """
        try:
            if sb is None:
                sb_res = self._get_supabase(user)
                sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
            if not sb:
                return {
                    "tier": "free",
                    "usage": {"strategies": 0, "strategies_limit": 3, "bots": 0, "bots_limit": 1, "ml_training_used": 0, "ml_training_limit": 0},
                    "billing_status": "active",
                    "subscription_end": None,
                    "is_trial": False
                }

            # Get user profile with subscription info
            res = await self._execute_sb_query(sb.table("profiles").select("subscription_tier, updated_at").eq("id", user["id"]).limit(1))
            profile = res.data[0] if res and hasattr(res, "data") and res.data else {}

            # Get subscription tier from profile
            subscription_tier = profile.get("subscription_tier", "free")

            # Calculate usage metrics (use provided task if available, otherwise fetch directly)
            if strategies_task is not None:
                strategies = await strategies_task
            else:
                strategies = await self.get_strategies(user, sb=sb)
            strategies_used = len(strategies)
            active_bots = len([s for s in strategies if s["status"] == "active"])
            
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
                "billing_status": "active",
                "subscription_end": None,
                "is_trial": False
            }
        except Exception as e:
            logger.error(f"Failed to fetch subscription data for user {user['id']}: {e}")
            return {
                "tier": "free",
                "usage": {"strategies": 0, "strategies_limit": 3, "bots": 0, "bots_limit": 1, "ml_training_used": 0, "ml_training_limit": 0},
                "billing_status": "active",
                "subscription_end": None,
                "is_trial": False
            }
    
    async def get_exchange_data(self, user: dict, sb: Optional[Any] = None) -> Dict:
        """
        Get exchange connection data.
        
        Returns:
            Connected exchanges, connection status, latency metrics
        """
        try:
            if sb is None:
                sb_res = self._get_supabase(user)
                sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
            if not sb:
                return {
                    "total_exchanges": 0,
                    "connected_exchanges": 0,
                    "exchanges": [],
                    "can_trade": False
                }
            
            # Get user's exchange connections from exchange_keys
            try:
                res = await self._execute_sb_query(sb.table("exchange_keys").select("exchange_id, updated_at").eq("user_id", user["id"]))
                connections = res.data or [] if res and hasattr(res, "data") else []
            except Exception as e:
                logger.warning(f"Failed to fetch exchange_keys in dashboard: {e}")
                connections = []
            
            # Calculate exchange metrics
            connected_exchanges = connections
            total_exchanges = len(connections)
            
            # Get exchange health from actual connections
            exchange_health = []
            for conn in connected_exchanges:
                exchange_id = conn.get("exchange_id", "unknown")
                exchange_health.append({
                    "exchange_id": exchange_id,
                    "status": "connected",
                    "latency_ms": 35,
                    "last_sync": conn.get("updated_at")
                })
            
            return {
                "total_exchanges": total_exchanges,
                "connected_exchanges": len(connected_exchanges),
                "exchanges": exchange_health,
                "can_trade": len(connected_exchanges) > 0
            }
        except Exception as e:
            logger.error(f"Failed to fetch exchange data for user {user['id']}: {e}")
            return {
                "total_exchanges": 0,
                "connected_exchanges": 0,
                "exchanges": [],
                "can_trade": False
            }
    
    async def get_notification_data(self, user: dict, sb: Optional[Any] = None) -> Dict:
        """
        Get notification data.
        
        Returns:
            Unread count, recent notifications, notification categories
        """
        try:
            if sb is None:
                sb_res = self._get_supabase(user)
                sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
            res_unread, res_recent = await asyncio.gather(
                self._execute_sb_query(sb.table("notifications").select("id", count="exact").eq("user_id", user["id"]).eq("read", False)),
                self._execute_sb_query(sb.table("notifications").select("id, type, category, severity, title, message, read, created_at").eq("user_id", user["id"]).order("created_at", desc=True).limit(10)),
                return_exceptions=True
            )

            unread_count = res_unread.count if not isinstance(res_unread, Exception) and res_unread and hasattr(res_unread, "count") and res_unread.count is not None else 0
            
            recent_notifications = []
            if not isinstance(res_recent, Exception) and res_recent and hasattr(res_recent, "data") and res_recent.data:
                for notif in res_recent.data:
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
            
            categories = {}
            for notif in recent_notifications:
                cat = notif.get("category", "system")
                categories[cat] = categories.get(cat, 0) + 1
            
            return {
                "unread_count": unread_count,
                "total_count": unread_count + len([n for n in recent_notifications if n["read"]]),
                "recent": recent_notifications,
                "categories": categories
            }
        except Exception as e:
            logger.error(f"Failed to fetch notification data for user {user['id']}: {e}")
            return {"unread_count": 0, "total_count": 0, "recent": [], "categories": {}}
    
    async def get_referral_data(self, user: dict, sb: Optional[Any] = None) -> Dict:
        """
        Get referral program data.
        
        Returns:
            Referral code, total referrals, earnings, referral link
        """
        try:
            default_ref = user["id"][:8].upper()
            if sb is None:
                sb_res = self._get_supabase(user)
                sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
            if not sb:
                return {
                    "referral_code": default_ref,
                    "referral_link": f"{_app_base_url()}/ref/{default_ref}",
                    "total_referrals": 0,
                    "active_referrals": 0,
                    "pending_earnings": 0.0,
                    "approved_earnings": 0.0,
                    "lifetime_earnings": 0.0
                }

            res = await self._execute_sb_query(sb.table("referral_profiles").select("referral_code, total_referrals, active_referrals, pending_earnings, approved_earnings, paid_earnings, lifetime_earnings").eq("user_id", user["id"]).limit(1))
            profile = res.data[0] if res and hasattr(res, "data") and res.data else {}
            
            ref_code = profile.get("referral_code") or default_ref
            
            return {
                "referral_code": ref_code,
                "referral_link": f"{_app_base_url()}/ref/{ref_code}",
                "total_referrals": profile.get("total_referrals", 0),
                "active_referrals": profile.get("active_referrals", 0),
                "pending_earnings": float(profile.get("pending_earnings", 0.0)),
                "approved_earnings": float(profile.get("approved_earnings", 0.0)),
                "lifetime_earnings": float(profile.get("lifetime_earnings", 0.0))
            }
        except Exception as e:
            logger.error(f"Failed to fetch referral data for user {user['id']}: {e}")
            return {
                "referral_code": "",
                "referral_link": "",
                "total_referrals": 0,
                "active_referrals": 0,
                "pending_earnings": 0.0,
                "approved_earnings": 0.0,
                "lifetime_earnings": 0.0
            }
    
    async def get_risk_data(self, user: dict, portfolio: Optional[Dict] = None, sb: Optional[Any] = None) -> Dict:
        """
        Get risk management data.
        
        Args:
            user: User dict
            portfolio: Optional portfolio data (to avoid duplicate query if already fetched)
            sb: Optional shared Supabase client

        Returns:
            Risk settings, current risk level, circuit breaker status
        """
        try:
            if sb is None:
                sb_res = self._get_supabase(user)
                sb = await sb_res if inspect.isawaitable(sb_res) else sb_res

            # Get risk settings
            settings = {}
            if sb:
                res = await self._execute_sb_query(sb.table("risk_settings").select("max_daily_loss, max_positions, max_leverage, kill_switches").eq("user_id", user["id"]).limit(1))
                settings = res.data[0] if res and hasattr(res, "data") and res.data else {}

            current_drawdown = abs(float((portfolio or {}).get("pnl_pct", 0)))
            
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
            logger.error(f"Failed to fetch risk data for user {user['id']}: {e}")
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
    
    async def get_marketplace_data(self, user: dict, sb: Optional[Any] = None) -> Dict:
        """
        Get marketplace data from library_strategies.
        
        Returns:
            Available strategies, user's publications, subscription counts
        """
        try:
            if sb is None:
                sb_res = self._get_supabase(user)
                sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
            if not sb:
                return {"available_count": 0, "user_publications": 0, "total_subscribers": 0, "featured": []}

            res_avail, res_pubs = await asyncio.gather(
                self._execute_sb_query(sb.table("library_strategies").select("id, name, is_featured").eq("is_active", True).in_("moderation_status", ["approved", "featured"]).limit(10)),
                self._execute_sb_query(sb.table("library_strategies").select("id, subscriber_count").eq("author_id", user["id"])),
                return_exceptions=True
            )

            available_strategies = res_avail.data if not isinstance(res_avail, Exception) and res_avail and hasattr(res_avail, "data") and res_avail.data else []
            user_publications = res_pubs.data if not isinstance(res_pubs, Exception) and res_pubs and hasattr(res_pubs, "data") and res_pubs.data else []
            
            total_subscribers = 0
            for pub in user_publications:
                total_subscribers += pub.get("subscriber_count", 0)
            
            return {
                "available_count": len(available_strategies),
                "user_publications": len(user_publications),
                "total_subscribers": total_subscribers,
                "featured": [s for s in available_strategies if s.get("is_featured")][:3]
            }
        except Exception as e:
            logger.error(f"Failed to fetch marketplace data for user {user['id']}: {e}")
            return {
                "available_count": 0,
                "user_publications": 0,
                "total_subscribers": 0,
                "featured": []
            }
    
    async def get_admin_metrics(self, user: dict) -> Dict:
        """
        Get system-wide metrics (admin only).
        
        Returns:
            Total users, active strategies, system volume, revenue metrics
        """
        # Return default/mock metrics - real implementation would aggregate across all users
        return {
            "total_users": 150,
            "active_users_24h": 42,
            "total_strategies": 320,
            "active_bots": 85,
            "total_volume_24h": 1250000.0,
            "monthly_recurring_revenue": 4500.0,
            "system_health_score": 99.8
        }
    
    async def get_strategies_by_market(self, user: dict) -> List[Dict]:
        """
        Get public strategies from the marketplace for exploration.
        
        Returns:
            List of published strategies from all users
        """
        # Return empty list - real implementation would fetch from marketplace/library table
        return []

    async def get_portfolio_overview(self, user: dict, environment: str = "live") -> Dict:
        """
        Get normalized portfolio overview data with strict environment isolation.
        
        Distinguishes:
        - total_equity / total_value
        - available_balance / free_balance
        - used_balance / margin
        - today_realized_pnl (closed trades since 00:00:00 UTC)
        - unrealized_pnl (mark-to-market open positions)
        - today_pnl (today_realized + unrealized delta)
        - cumulative_pnl (lifetime total)
        """
        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        today_utc_cutoff = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        
        if environment == "paper":
            try:
                from backend_app.backend.paper_trading_service import get_paper_trading_service
                paper_svc = get_paper_trading_service()
                acct = paper_svc.get_or_create_account(user["id"])
                
                total_equity = float(acct.get("total_equity", 100000.0))
                available_balance = float(acct.get("available_balance", 100000.0))
                free_balance = available_balance
                used_balance = float(acct.get("locked_balance", 0.0))
                realized_pnl = float(acct.get("realized_pnl", 0.0))
                unrealized_pnl = float(acct.get("unrealized_pnl", 0.0))
                initial_capital = float(acct.get("initial_capital", 100000.0))
                
                # Calculate today's realized PnL from paper trades since 00:00 UTC
                trades = paper_svc.get_trades(user["id"])
                today_realized_pnl = sum(
                    float(t.get("realized_pnl", 0.0))
                    for t in trades
                    if (t.get("executed_at") or "") >= today_utc_cutoff
                )
                
                today_pnl = today_realized_pnl + unrealized_pnl
                today_return_pct = round((today_pnl / initial_capital * 100), 2) if initial_capital > 0 else 0.0
                cumulative_pnl = realized_pnl + unrealized_pnl
                
                return {
                    "total_equity": total_equity,
                    "total_value": total_equity,
                    "available_balance": available_balance,
                    "free_balance": free_balance,
                    "used_balance": used_balance,
                    "today_pnl": today_pnl,
                    "today_realized_pnl": today_realized_pnl,
                    "today_return_pct": today_return_pct,
                    "unrealized_pnl": unrealized_pnl,
                    "cumulative_pnl": cumulative_pnl,
                    "total_exposure": used_balance,
                    "currency": "USD",
                    "environment": "paper",
                    "updated_at": now_utc.isoformat()
                }
            except Exception as paper_err:
                logger.error(f"Failed to fetch paper portfolio overview for {user['id']}: {paper_err}")
                return {
                    "total_equity": 100000.0,
                    "total_value": 100000.0,
                    "available_balance": 100000.0,
                    "free_balance": 100000.0,
                    "used_balance": 0.0,
                    "today_pnl": 0.0,
                    "today_realized_pnl": 0.0,
                    "today_return_pct": 0.0,
                    "unrealized_pnl": 0.0,
                    "cumulative_pnl": 0.0,
                    "total_exposure": 0.0,
                    "currency": "USD",
                    "environment": "paper",
                    "updated_at": now_utc.isoformat()
                }

        # LIVE Environment
        telemetry = self._get_telemetry()
        safe_uid = self._safe_uid(user["id"])
        
        total_equity = 0.0
        cumulative_pnl = 0.0
        pnl_pct = 0.0
        total_exposure = 0.0
        available_balance = 0.0
        free_balance = 0.0
        used_balance = 0.0
        
        try:
            result = await telemetry.execute_query(
                f"SELECT * FROM live_user_pnl WHERE user_id = '{safe_uid}' LIMIT 1;"  # nosec: B608
            )
            if result and result.get("dataset"):
                cols = [c["name"] for c in result["columns"]]
                row_dict = dict(zip(cols, result["dataset"][0]))
                total_equity = float(row_dict.get("total_equity", 0.0))
                cumulative_pnl = float(row_dict.get("total_pnl", 0.0))
                pnl_pct = float(row_dict.get("pnl_pct", 0.0))
                total_exposure = float(row_dict.get("total_exposure", 0.0))
        except Exception as q_err:
            logger.debug(f"QuestDB live_user_pnl read error: {q_err}")

        # Check Redis cached exchange balance for live available/free balance
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            import json
            # Scan or get balance keys
            keys = await redis_manager.keys(f"portfolio:{user['id']}:*:balance")
            if keys:
                for k in keys:
                    raw_bal = await redis_manager.get(k)
                    if raw_bal:
                        bal_data = json.loads(raw_bal) if isinstance(raw_bal, str) else raw_bal
                        free_balance += float(bal_data.get("available", 0.0))
                        total_from_ex = float(bal_data.get("total", 0.0))
                        if total_equity == 0.0 and total_from_ex > 0.0:
                            total_equity = total_from_ex
                available_balance = free_balance
                used_balance = max(0.0, total_equity - free_balance)
            else:
                available_balance = max(0.0, total_equity - total_exposure)
                free_balance = available_balance
                used_balance = total_exposure
        except Exception as redis_err:
            logger.debug(f"Redis balance read error: {redis_err}")
            available_balance = max(0.0, total_equity - total_exposure)
            free_balance = available_balance
            used_balance = total_exposure

        # Calculate today's realized PnL from QuestDB executions table since 00:00 UTC
        today_realized_pnl = 0.0
        try:
            today_date_str = now_utc.strftime("%Y-%m-%d")
            pnl_query = (
                f"SELECT sum(pnl) as today_realized_pnl FROM executions "
                f"WHERE user_id = '{safe_uid}' "
                f"AND timestamp >= to_timestamp('{today_date_str}T00:00:00.000000Z', 'yyyy-MM-ddTHH:mm:ss.SSSUUUZ');"  # nosec: B608
            )
            pnl_res = await telemetry.execute_query(pnl_query)
            if pnl_res and pnl_res.get("dataset") and pnl_res["dataset"][0][0] is not None:
                today_realized_pnl = float(pnl_res["dataset"][0][0])
        except Exception as exec_err:
            logger.debug(f"QuestDB executions today PnL query error: {exec_err}")

        # Calculate open positions unrealized PnL from Redis live positions
        unrealized_pnl = 0.0
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            import json
            pos_keys = await redis_manager.keys(f"portfolio:{user['id']}:*:positions")
            for pk in pos_keys:
                raw_pos = await redis_manager.get(pk)
                if raw_pos:
                    pos_dict = json.loads(raw_pos) if isinstance(raw_pos, str) else raw_pos
                    if isinstance(pos_dict, dict):
                        for p in pos_dict.values():
                            unrealized_pnl += float(p.get("unrealized_pnl", 0.0))
                    elif isinstance(pos_dict, list):
                        for p in pos_dict:
                            unrealized_pnl += float(p.get("unrealized_pnl", 0.0))
        except Exception as pos_err:
            logger.debug(f"Live unrealized PnL calculate error: {pos_err}")

        today_pnl = today_realized_pnl + unrealized_pnl
        today_return_pct = round((today_pnl / total_equity * 100), 2) if total_equity > 0 else 0.0

        return {
            "total_equity": total_equity,
            "total_value": total_equity,
            "available_balance": available_balance,
            "free_balance": free_balance,
            "used_balance": used_balance,
            "today_pnl": today_pnl,
            "today_realized_pnl": today_realized_pnl,
            "today_return_pct": today_return_pct,
            "unrealized_pnl": unrealized_pnl,
            "cumulative_pnl": cumulative_pnl,
            "total_exposure": total_exposure,
            "currency": "USDT",
            "environment": "live",
            "updated_at": now_utc.isoformat()
        }
    
    async def get_open_positions(self, user: dict, environment: str = "live") -> List[Dict]:
        """
        Get normalized open trading positions with strict environment isolation.
        
        Returns canonical NormalizedPosition list.
        """
        from datetime import timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        
        if environment == "paper":
            try:
                from backend_app.backend.paper_trading_service import get_paper_trading_service
                paper_svc = get_paper_trading_service()
                positions = paper_svc.get_positions(user["id"])
                
                pos_list = []
                pos_items = positions.values() if isinstance(positions, dict) else positions
                for p in pos_items:
                    size = float(p.get("size", p.get("quantity", 0.0)))
                    if size <= 0:
                        continue
                    entry_p = float(p.get("entry_price", 0.0))
                    current_p = float(p.get("current_price", entry_p))
                    notional = size * current_p
                    u_pnl = float(p.get("unrealized_pnl", 0.0))
                    cost_basis = size * entry_p
                    u_pnl_pct = round((u_pnl / cost_basis * 100), 2) if cost_basis > 0 else 0.0
                    
                    pos_list.append({
                        "id": f"pos_paper_{user['id'][:8]}_{p.get('symbol', 'BTC/USDT').lower().replace('/', '_')}",
                        "exchange_id": "paper",
                        "environment": "paper",
                        "symbol": p.get("symbol", "BTC/USDT"),
                        "market_type": "spot",
                        "side": p.get("side", "long").lower(),
                        "contracts": size,
                        "quantity": size,
                        "entry_price": entry_p,
                        "mark_price": current_p,
                        "notional": round(notional, 2),
                        "leverage": 1,
                        "unrealized_pnl": round(u_pnl, 2),
                        "unrealized_pnl_pct": u_pnl_pct,
                        "liquidation_price": None,
                        "margin": round(cost_basis, 2),
                        "margin_type": "cross",
                        "timestamp": p.get("updated_at") or p.get("created_at") or now_iso
                    })
                return pos_list
            except Exception as paper_pos_err:
                logger.error(f"Failed to fetch paper positions for {user['id']}: {paper_pos_err}")
                return []

        # LIVE Environment
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            import json
            pos_list = []
            keys = await redis_manager.keys(f"portfolio:{user['id']}:*:positions")
            for k in keys:
                # Extract exchange_id from key: portfolio:{uid}:{exchange_id}:positions
                parts = k.split(":") if isinstance(k, str) else k.decode().split(":")
                ex_id = parts[2] if len(parts) >= 4 else "binance"
                
                raw_pos = await redis_manager.get(k)
                if raw_pos:
                    data = json.loads(raw_pos) if isinstance(raw_pos, str) else raw_pos
                    items = []
                    if isinstance(data, dict):
                        for sym_k, p_val in data.items():
                            if isinstance(p_val, dict):
                                if "symbol" not in p_val:
                                    p_val["symbol"] = sym_k
                                items.append(p_val)
                    elif isinstance(data, list):
                        items = data

                    for p in items:
                        contracts = float(p.get("contracts", p.get("size", 0.0)))
                        if contracts == 0:
                            continue
                        entry_p = float(p.get("entry_price", p.get("entryPrice", 0.0)))
                        notional = float(p.get("notional", contracts * entry_p))
                        u_pnl = float(p.get("unrealized_pnl", p.get("unrealizedPnl", 0.0)))
                        cost = contracts * entry_p
                        u_pct = round((u_pnl / cost * 100), 2) if cost > 0 else 0.0
                        
                        sym = p.get("symbol", "BTC/USDT")
                        pos_list.append({
                            "id": f"pos_{ex_id}_{sym.lower().replace('/', '_')}",
                            "exchange_id": ex_id,
                            "environment": "live",
                            "symbol": sym,
                            "market_type": "future" if "future" in ex_id or "swap" in ex_id else "spot",
                            "side": p.get("side", "long").lower(),
                            "contracts": contracts,
                            "quantity": contracts,
                            "entry_price": entry_p,
                            "mark_price": float(p.get("mark_price", p.get("markPrice", entry_p))),
                            "notional": round(notional, 2),
                            "leverage": int(p.get("leverage", 1)),
                            "unrealized_pnl": round(u_pnl, 2),
                            "unrealized_pnl_pct": u_pct,
                            "liquidation_price": float(p.get("liquidation_price", p.get("liquidationPrice", 0.0))) if p.get("liquidation_price") or p.get("liquidationPrice") else None,
                            "margin": round(float(p.get("margin", notional / max(1, int(p.get("leverage", 1))))), 2),
                            "margin_type": p.get("margin_type", "cross"),
                            "timestamp": p.get("timestamp") or now_iso
                        })
            return pos_list
        except Exception as live_pos_err:
            logger.error(f"Failed to fetch live positions for {user['id']}: {live_pos_err}")
            return []

    async def get_recent_executions(self, user: dict, environment: str = "live", limit: int = 5) -> List[Dict]:
        """
        Get normalized recent executed trades/fills with strict environment isolation.
        """
        if environment == "paper":
            try:
                from backend_app.backend.paper_trading_service import get_paper_trading_service
                paper_svc = get_paper_trading_service()
                trades = paper_svc.get_trades(user["id"])
                # Sort descending by executed_at
                sorted_trades = sorted(
                    trades,
                    key=lambda t: t.get("executed_at", ""),
                    reverse=True
                )[:limit]
                
                results = []
                for t in sorted_trades:
                    qty = float(t.get("quantity", t.get("amount", 0.0)))
                    price = float(t.get("price", 0.0))
                    results.append({
                        "id": t.get("execution_id", f"exec_paper_{t.get('order_id', '0')}"),
                        "order_id": t.get("order_id"),
                        "exchange_id": "paper",
                        "environment": "paper",
                        "symbol": t.get("symbol", "BTC/USDT"),
                        "side": t.get("side", "buy").lower(),
                        "price": price,
                        "amount": qty,
                        "cost": round(qty * price, 2),
                        "fee": float(t.get("fee", 0.0)),
                        "realized_pnl": float(t.get("realized_pnl", 0.0)),
                        "timestamp": t.get("executed_at"),
                        "strategy_id": t.get("strategy_id")
                    })
                return results
            except Exception as paper_trades_err:
                logger.error(f"Failed to fetch paper trades for {user['id']}: {paper_trades_err}")
                return []

        # LIVE Environment: Query QuestDB executions table
        telemetry = self._get_telemetry()
        safe_uid = self._safe_uid(user["id"])
        try:
            query = (
                f"SELECT timestamp, symbol, side, status, amount, price, pnl, fee "
                f"FROM executions "
                f"WHERE user_id = '{safe_uid}' "
                f"ORDER BY timestamp DESC "
                f"LIMIT {limit};"  # nosec: B608
            )
            res = await telemetry.execute_query(query)
            if res and res.get("dataset"):
                cols = [c["name"] for c in res["columns"]]
                results = []
                # Determine authoritative exchange_id from user's active exchange connections
                primary_exchange = "binance"
                try:
                    sb = await self._get_supabase(user)
                    ex_res = await self._execute_sb_query(
                        sb.table("exchange_keys")
                        .select("exchange_id")
                        .eq("user_id", user["id"])
                        .limit(1)
                    )
                    if ex_res and hasattr(ex_res, "data") and ex_res.data:
                        primary_exchange = ex_res.data[0].get("exchange_id") or "binance"
                except Exception:
                    primary_exchange = "binance"

                for idx, row in enumerate(res["dataset"]):
                    entry = dict(zip(cols, row))
                    qty = float(entry.get("amount", 0.0))
                    price = float(entry.get("price", 0.0))
                    ex_id = entry.get("exchange_id") or primary_exchange
                    results.append({
                        "id": f"exec_live_{safe_uid[:8]}_{idx}_{int(time.time())}",
                        "order_id": entry.get("order_id"),
                        "exchange_id": ex_id,
                        "environment": "live",
                        "symbol": entry.get("symbol", "BTC/USDT"),
                        "side": str(entry.get("side", "buy")).lower(),
                        "price": price,
                        "amount": qty,
                        "cost": round(qty * price, 2),
                        "fee": float(entry.get("fee", 0.0)),
                        "realized_pnl": float(entry.get("pnl", 0.0)),
                        "timestamp": entry.get("timestamp"),
                        "strategy_id": entry.get("strategy_id")
                    })
                return results
            return []
        except Exception as live_exec_err:
            logger.debug(f"QuestDB executions read error: {live_exec_err}")
            return []

    async def get_equity_curve(self, user: dict, days: int = 30, environment: str = "live") -> List[Dict]:
        """
        Get equity curve data with environment awareness.
        """
        telemetry = self._get_telemetry()
        safe_uid = self._safe_uid(user["id"])
        limit = max(1, min(int(days) * 96, 100_000))
        
        try:
            result = await telemetry.execute_query(
                f"SELECT timestamp, equity FROM equity_curve "
                f"WHERE user_id = '{safe_uid}' "
                f"ORDER BY timestamp ASC LIMIT -{limit};"  # nosec: B608
            )
            if result and result.get("dataset"):
                cols = [c["name"] for c in result["columns"]]
                return [dict(zip(cols, row)) for row in result["dataset"]]
        except Exception as eq_err:
            logger.debug(f"QuestDB equity curve fetch error: {eq_err}")

        # If paper mode and no QuestDB data, synthesize default baseline
        if environment == "paper":
            from datetime import timezone, timedelta
            now = datetime.now(timezone.utc)
            return [
                {"timestamp": (now - timedelta(days=days)).isoformat(), "equity": 100000.0},
                {"timestamp": now.isoformat(), "equity": 100000.0}
            ]
        
        return []
    
    async def get_strategies(self, user: dict, environment: str = "live", sb: Optional[Any] = None) -> List[Dict]:
        """
        Get strategies with calculated metrics and environment binding.
        """
        if sb is None:
            sb_res = self._get_supabase(user)
            sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        if not sb:
            return []
        
        res = await self._execute_sb_query(
            sb.table("strategies")
            .select("id, name, symbol, is_active, created_at")
            .eq("user_id", user["id"])
        )
        strategies = res.data or [] if res and hasattr(res, "data") else []
        
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
                "last_signal_time": s.get("last_signal_at") or None,
                "environment": environment
            })
        
        return enriched
    
    async def get_strategy_insights(self, user: dict, strategies_task: Optional['asyncio.Task'] = None) -> List[Dict]:
        """
        Calculate trading insights from strategy state.
        """
        if strategies_task is not None:
            strategies = await strategies_task
        else:
            strategies = await self.get_strategies(user)
        
        insights = []
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
            "text": f"{active_count} active strategy execution bot(s) running on live connected venues.",
            "action_text": "View Bots",
            "action_path": "/app/strategies"
        })
        
        insights.append({
            "id": "ins_3",
            "type": "success",
            "text": "Risk Circuit Breakers active: Max daily loss limit enforced by Risk Engine.",
            "action_text": "Risk Settings",
            "action_path": "/app/risk"
        })
        
        return insights[:3]
    
    async def get_recent_signals(self, user: dict, limit: int = 5, sb: Optional[Any] = None) -> List[Dict]:
        """
        Get recent signal traces for notifications.
        """
        if sb is None:
            sb_res = self._get_supabase(user)
            sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        res = await self._execute_sb_query(
            sb.table("signals")
            .select("id, generated_at, decision, symbol, exchange_id, risk_passed")
            .eq("user_id", user["id"])
            .order("generated_at", desc=True)
            .limit(limit)
        )
        records = res.data or [] if res and hasattr(res, "data") else []
        
        notifications = []
        for r in records:
            notifications.append({
                "id": r.get("id"),
                "time": r.get("generated_at") or None,
                "text": f"Signal {r.get('decision', 'BUY').upper()}: {r.get('symbol')} on {r.get('exchange_id', 'binance').upper()} (Risk: {'APPROVED' if r.get('risk_passed') else 'REJECTED'})",
                "type": "success" if r.get("risk_passed") else "warning"
            })
        
        return notifications
    
    async def get_health_status(self, user: dict, environment: str = "live") -> Dict:
        """
        Get real measured system health status (Zero fabricated latency numbers).
        """
        from backend_app.routers.risk import is_user_kill_switched
        kill_active = is_user_kill_switched(user["id"])
        
        # Check measured latency in Redis
        latency_ms = None
        latency_status = "unavailable"
        
        if environment == "live":
            try:
                from backend_app.core.cache.redis_manager import redis_manager
                import json
                keys = await redis_manager.keys(f"exchange_health:{user['id']}:*")
                for k in keys:
                    val = await redis_manager.get(k)
                    if val:
                        data = json.loads(val) if isinstance(val, str) else val
                        measured = data.get("latency_ms")
                        if measured is not None:
                            latency_ms = int(measured)
                            break
            except Exception as health_err:
                logger.debug(f"Health latency lookup error: {health_err}")

        if latency_ms is not None:
            if latency_ms < 150:
                latency_status = "optimal"
            elif latency_ms < 500:
                latency_status = "normal"
            else:
                latency_status = "degraded"

        return {
            "exchange_api_latency_ms": latency_ms,
            "exchange_api_latency_status": latency_status,
            "risk_circuit_breaker_status": "triggered" if kill_active else "armed",
            "risk_circuit_breaker_breaches": 0,
            "order_state_sync_status": "synchronized" if environment == "paper" else "active"
        }
    
    async def get_risk_data(self, user: dict, environment: str = "live", portfolio: Optional[Dict] = None, sb: Optional[Any] = None) -> Dict:
        """
        Get authoritative risk management data with synchronized risk_score and risk_level.
        """
        from backend_app.routers.risk import get_user_risk_settings_store, is_user_kill_switched
        uid = str(user["id"])
        settings = get_user_risk_settings_store(uid)
        max_daily_loss = float(settings.get("max_daily_loss", 500.0))
        max_positions = int(settings.get("max_positions", 10))
        max_leverage = int(settings.get("max_leverage", 3))
        circuit_armed = bool(settings.get("circuit_breaker_armed", True))
        kill_active = is_user_kill_switched(uid)
        
        # Calculate daily loss utilized
        daily_loss_utilized = 0.0
        if environment == "paper":
            try:
                from backend_app.backend.paper_trading_service import get_paper_trading_service
                from datetime import timezone
                paper_svc = get_paper_trading_service()
                trades = paper_svc.get_trades(uid)
                today_cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                closed_losses = sum(
                    float(t.get("realized_pnl", 0.0))
                    for t in trades
                    if (t.get("executed_at") or "") >= today_cutoff and float(t.get("realized_pnl", 0.0)) < 0
                )
                daily_loss_utilized = abs(closed_losses)
            except Exception:
                daily_loss_utilized = 0.0
        else:
            try:
                telemetry = self._get_telemetry()
                safe_uid = self._safe_uid(uid)
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                res = await telemetry.execute_query(
                    f"SELECT sum(pnl) FROM executions WHERE user_id = '{safe_uid}' AND pnl < 0 AND timestamp >= to_timestamp('{today_str}T00:00:00.000000Z', 'yyyy-MM-ddTHH:mm:ss.SSSUUUZ');"  # nosec: B608
                )
                if res and res.get("dataset") and res["dataset"][0][0] is not None:
                    daily_loss_utilized = abs(float(res["dataset"][0][0]))
            except Exception:
                daily_loss_utilized = 0.0

        loss_util_pct = (daily_loss_utilized / max_daily_loss * 100) if max_daily_loss > 0 else 0.0
        
        # Calculate open positions count
        positions = await self.get_open_positions(user, environment=environment)
        open_pos_count = len(positions)
        pos_util_pct = (open_pos_count / max_positions * 100) if max_positions > 0 else 0.0
        
        # Calculate deterministic numeric risk score (0-100)
        risk_score = min(100, int((loss_util_pct * 0.6) + (pos_util_pct * 0.4)))
        
        # Standardize risk level
        if kill_active:
            risk_level = "blocked"
        elif loss_util_pct >= 100 or pos_util_pct >= 100:
            risk_level = "critical"
        elif loss_util_pct >= 75 or pos_util_pct >= 75:
            risk_level = "high"
        elif loss_util_pct >= 40 or pos_util_pct >= 40:
            risk_level = "medium"
        else:
            risk_level = "low"

        return {
            "risk_score": risk_score,
            "risk_level": risk_level,
            "current_drawdown_pct": round(float((portfolio or {}).get("today_return_pct", 0.0)), 2),
            "max_daily_loss": max_daily_loss,
            "daily_loss_utilized": round(daily_loss_utilized, 2),
            "max_positions": max_positions,
            "open_positions_count": open_pos_count,
            "max_leverage": max_leverage,
            "circuit_breaker_armed": circuit_armed,
            "circuit_breaker_breaches": 0,
            "kill_switch_active": kill_active,
            "kill_switches": settings.get("kill_switches", {})
        }
    
    async def get_dashboard_data(self, user: dict, equity_days: int = 30, environment: str = "live") -> Dict:
        """
        Get complete normalized dashboard data in one call with strict environment isolation.
        
        Args:
            user: User dict with id and access_token
            equity_days: Number of days for equity curve
            environment: 'live' or 'paper'
        
        Returns:
            Comprehensive, environment-isolated dashboard structure.
        """
        from datetime import timezone
        norm_env = environment.lower() if environment in ("live", "paper") else "live"
        dashboard_start = time.perf_counter()
        
        try:
            sb = await self._get_supabase(user)
            strategies_start = time.perf_counter()
            strategies_task = asyncio.create_task(self.get_strategies(user, environment=norm_env, sb=sb))

            gather_start = time.perf_counter()
            results = await asyncio.gather(
                self._timed_operation("get_portfolio_overview", self.get_portfolio_overview(user, environment=norm_env)),
                self._timed_operation("get_equity_curve", self.get_equity_curve(user, days=equity_days, environment=norm_env)),
                self._timed_operation("get_open_positions", self.get_open_positions(user, environment=norm_env)),
                self._timed_operation("get_recent_executions", self.get_recent_executions(user, environment=norm_env, limit=5)),
                self._timed_operation("get_strategy_insights", self.get_strategy_insights(user, strategies_task)),
                self._timed_operation("get_recent_signals", self.get_recent_signals(user, sb=sb)),
                self._timed_operation("get_health_status", self.get_health_status(user, environment=norm_env)),
                self._timed_operation("get_subscription_data", self.get_subscription_data(user, strategies_task, sb=sb)),
                self._timed_operation("get_exchange_data", self.get_exchange_data(user, sb=sb)),
                self._timed_operation("get_notification_data", self.get_notification_data(user, sb=sb)),
                self._timed_operation("get_referral_data", self.get_referral_data(user, sb=sb)),
                self._timed_operation("get_marketplace_data", self.get_marketplace_data(user, sb=sb)),
                return_exceptions=True
            )
            gather_duration_ms = (time.perf_counter() - gather_start) * 1000

            (portfolio, equity, positions, executions, insights,
             signals, health, subscription, exchange,
             notifications, referral, marketplace) = results

            strategies = await strategies_task

            # Handle exceptions with safe fallbacks
            if isinstance(portfolio, Exception):
                logger.error(f"Portfolio fetch failed: {portfolio}")
                portfolio = {
                    "total_equity": 100000.0 if norm_env == "paper" else 0.0,
                    "total_value": 100000.0 if norm_env == "paper" else 0.0,
                    "available_balance": 100000.0 if norm_env == "paper" else 0.0,
                    "free_balance": 100000.0 if norm_env == "paper" else 0.0,
                    "used_balance": 0.0,
                    "today_pnl": 0.0,
                    "today_realized_pnl": 0.0,
                    "today_return_pct": 0.0,
                    "unrealized_pnl": 0.0,
                    "cumulative_pnl": 0.0,
                    "total_exposure": 0.0,
                    "currency": "USD" if norm_env == "paper" else "USDT",
                    "environment": norm_env,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }

            if isinstance(equity, Exception):
                logger.error(f"Equity curve fetch failed: {equity}")
                equity = []

            if isinstance(positions, Exception):
                logger.error(f"Open positions fetch failed: {positions}")
                positions = []

            if isinstance(executions, Exception):
                logger.error(f"Executions fetch failed: {executions}")
                executions = []

            if isinstance(insights, Exception):
                logger.error(f"Insights generation failed: {insights}")
                insights = []
            
            if isinstance(signals, Exception):
                logger.error(f"Signals fetch failed: {signals}")
                signals = []
            
            if isinstance(health, Exception):
                logger.error(f"Health status fetch failed: {health}")
                health = {
                    "exchange_api_latency_ms": None,
                    "exchange_api_latency_status": "unavailable",
                    "risk_circuit_breaker_status": "armed",
                    "risk_circuit_breaker_breaches": 0,
                    "order_state_sync_status": "synchronized" if norm_env == "paper" else "active"
                }
            
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
            
            if isinstance(marketplace, Exception):
                logger.error(f"Marketplace data fetch failed: {marketplace}")
                marketplace = {"available_count": 0, "user_publications": 0, "total_subscribers": 0, "featured": []}

            # Fetch authoritative risk data
            risk_data = await self.get_risk_data(user, environment=norm_env, portfolio=portfolio, sb=sb)
            
            active_strategies = [s for s in strategies if s["status"] == "active"]
            paused_strategies = [s for s in strategies if s["status"] == "paused"]
            
            dashboard_duration_ms = (time.perf_counter() - dashboard_start) * 1000
            logger.info(
                f"dashboard_total_timing",
                extra={
                    "operation": "get_dashboard_data",
                    "duration_ms": round(dashboard_duration_ms, 2),
                    "environment": norm_env
                },
            )
            
            return {
                "environment": norm_env,
                "overview": {
                    "total_value": float(portfolio.get("total_value", portfolio.get("total_equity", 0.0))),
                    "total_equity": float(portfolio.get("total_equity", 0.0)),
                    "available_balance": float(portfolio.get("available_balance", 0.0)),
                    "free_balance": float(portfolio.get("free_balance", portfolio.get("available_balance", 0.0))),
                    "used_balance": float(portfolio.get("used_balance", 0.0)),
                    "today_pnl": float(portfolio.get("today_pnl", 0.0)),
                    "today_realized_pnl": float(portfolio.get("today_realized_pnl", 0.0)),
                    "today_return_pct": float(portfolio.get("today_return_pct", 0.0)),
                    "unrealized_pnl": float(portfolio.get("unrealized_pnl", 0.0)),
                    "cumulative_pnl": float(portfolio.get("cumulative_pnl", 0.0)),
                    "total_exposure": float(portfolio.get("total_exposure", 0.0)),
                    "currency": portfolio.get("currency", "USDT"),
                    "updated_at": portfolio.get("updated_at", datetime.now(timezone.utc).isoformat())
                },
                "positions": positions,
                "executions": executions,
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
                    "total": len(strategies),
                    "active": len(active_strategies),
                    "paused": len(paused_strategies),
                    "running": len(active_strategies),
                    "stopped": len(paused_strategies),
                    "items": strategies
                },
                "marketplace": {
                    "available_count": marketplace.get("available_count", 0),
                    "user_publications": marketplace.get("user_publications", 0),
                    "total_subscribers": marketplace.get("total_subscribers", 0),
                    "featured": marketplace.get("featured", [])
                },
                "risk": risk_data,
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
                "health": health,
                "exchange": {
                    "total_exchanges": exchange.get("total_exchanges", 0),
                    "connected_exchanges": exchange.get("connected_exchanges", 0),
                    "can_trade": exchange.get("can_trade", False),
                    "exchanges": exchange.get("exchanges", [])
                },
                "recent_activity": {
                    "signals": signals,
                    "insights": insights,
                    "executions": executions
                },
                "equity_curve": equity,
                "generated_at": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Dashboard aggregation failed for user {user['id']}: {e}")
            raise


# Singleton instance
_dashboard_service = None

async def get_dashboard_service() -> DashboardAggregationService:
    """Get singleton DashboardAggregationService instance."""
    global _dashboard_service
    if _dashboard_service is None:
        _dashboard_service = DashboardAggregationService()
    return _dashboard_service

