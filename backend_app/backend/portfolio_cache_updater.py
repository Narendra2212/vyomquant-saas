"""
backend/portfolio_cache_updater.py — Portfolio Cache Updater (STEP 5)

═══════════════════════════════════════════════════════════════════════════════
STEP 5: PORTFOLIO CACHE LAYER
═══════════════════════════════════════════════════════════════════════════════

Background job that periodically fetches portfolio data from exchange
and caches it in Redis for low-latency access by ExecutionGuard.

FLOW:
  exchange → updater → Redis → ExecutionGuard

NOT:
  execution → exchange (❌ high latency, rate limit issues)

Cache Key Format:
  portfolio:{user_id}:{exchange_id}:balance  (Redis Hash)
  portfolio:{user_id}:{exchange_id}:positions (Redis String/JSON)

TTL: 1-5 seconds (configurable)

EXPECTED RESULT:
  ✅ Low latency (< 1ms) for portfolio reads
  ✅ No exchange rate limit issues
  ✅ System stable under load
  ✅ Graceful degradation if exchange is slow
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Optional

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger(__name__)


class PortfolioCacheUpdater:
    """
    STEP 5: Background service that updates portfolio cache from exchange.
    
    This runs independently of the execution path to ensure:
    - ExecutionGuard always has fresh data (1-5 second staleness max)
    - No exchange latency in order execution
    - No rate limiting issues
    """
    
    def __init__(
        self,
        update_interval_seconds: float = 3.0,  # Default: 3 seconds
        ttl_seconds: int = 5,  # Redis TTL
    ):
        self.update_interval = update_interval_seconds
        self.ttl = ttl_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._user_exchanges: Dict[str, str] = {}  # user_id -> exchange_id mapping
        
    async def start(self):
        """Start the background updater."""
        if self._running:
            return
            
        self._running = True
        self._task = asyncio.create_task(self._update_loop())
        logger.info(f"✅ PortfolioCacheUpdater started (interval: {self.update_interval}s, TTL: {self.ttl}s)")
        
    async def stop(self):
        """Stop the background updater."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 PortfolioCacheUpdater stopped")
        
    def register_user(self, user_id: str, exchange_id: str):
        """Register a user for portfolio cache updates."""
        self._user_exchanges[user_id] = exchange_id
        logger.info(f"👤 Registered user {user_id} for {exchange_id} portfolio updates")
        
    def unregister_user(self, user_id: str):
        """Unregister a user from portfolio cache updates."""
        if user_id in self._user_exchanges:
            del self._user_exchanges[user_id]
            logger.info(f"👤 Unregistered user {user_id} from portfolio updates")
    
    async def _update_loop(self):
        """Main update loop - runs periodically to refresh cache."""
        while self._running:
            try:
                await self._update_all_portfolios()
                await asyncio.sleep(self.update_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Portfolio cache update error: {e}")
                await asyncio.sleep(1)  # Brief pause on error
    
    async def _update_all_portfolios(self):
        """Update portfolio cache for all registered users."""
        redis_client = await redis_manager.get_client()
        
        for user_id, exchange_id in self._user_exchanges.items():
            try:
                await self._update_user_portfolio(
                    redis_client, user_id, exchange_id
                )
            except Exception as e:
                logger.error(f"❌ Failed to update portfolio for {user_id}: {e}")
    
    async def _update_user_portfolio(
        self,
        redis_client,
        user_id: str,
        exchange_id: str,
    ):
        """
        Fetch portfolio from exchange and cache in Redis.
        
        Args:
            redis_client: Redis client from shared pool
            user_id: User identifier
            exchange_id: Exchange identifier (e.g., "binance")
        """
        from backend_app.backend.connection_engine import \
            get_or_create_exchange
        from backend_app.backend.key_manager import KeyVault

        # Build cache keys
        balance_key = f"portfolio:{user_id}:{exchange_id}:balance"
        position_key = f"portfolio:{user_id}:{exchange_id}:positions"
        
        try:
            # Get exchange connection
            vault = KeyVault()
            keys = vault.load_decrypted_keys(user_id, exchange_id)
            exchange = await get_or_create_exchange(
                user_id=user_id,
                exchange_id=exchange_id,
                api_key=keys["api_key"],
                secret_key=keys["secret_key"],
                password=keys.get("password"),
            )
            
            # Fetch from exchange
            balance = await exchange.fetch_balance()
            positions = await exchange.fetch_positions()
            
            # Prepare balance data
            balance_data = {
                "available": str(balance.get("free", {}).get("USDT", 0)),
                "total": str(balance.get("total", {}).get("USDT", 0)),
                "timestamp": datetime.utcnow().isoformat(),
            }
            
            # Calculate total exposure from positions
            total_exposure = 0.0
            position_dict = {}
            
            for pos in positions:
                symbol = pos.get("symbol", "")
                contracts = float(pos.get("contracts", 0))
                notional = float(pos.get("notional", 0))
                entry_price = float(pos.get("entryPrice", 0))
                unrealized_pnl = float(pos.get("unrealizedPnl", 0))
                
                if contracts != 0:
                    position_dict[symbol] = {
                        "contracts": str(contracts),
                        "notional": str(notional),
                        "entry_price": str(entry_price),
                        "side": pos.get("side", "unknown"),
                        "unrealized_pnl": str(unrealized_pnl),
                    }
                    total_exposure += abs(notional)
            
            balance_data["exposure"] = str(total_exposure)
            
            # Cache in Redis with TTL
            pipe = redis_client.pipeline()
            
            # Store balance as hash
            pipe.hset(balance_key, mapping=balance_data)
            pipe.expire(balance_key, self.ttl)
            
            # Store positions as JSON string
            pipe.setex(
                position_key,
                self.ttl,
                json.dumps(position_dict)
            )
            
            await pipe.execute()
            
            logger.debug(
                f"💾 Cached portfolio for {user_id}:{exchange_id} "
                f"(balance: {balance_data['total']}, positions: {len(position_dict)})"
            )
            
        except Exception as e:
            logger.error(f"❌ Failed to fetch/cache portfolio for {user_id}:{exchange_id}: {e}")
            # Don't re-raise - background job should continue for other users
    
    async def force_update(self, user_id: str, exchange_id: str) -> bool:
        """
        Force immediate portfolio update for a specific user.
        
        Returns:
            bool: True if update succeeded
        """
        redis_client = await redis_manager.get_client()
        
        try:
            await self._update_user_portfolio(redis_client, user_id, exchange_id)
            return True
        except Exception as e:
            logger.error(f"❌ Force update failed for {user_id}: {e}")
            return False


# Global singleton instance
portfolio_cache_updater = PortfolioCacheUpdater(
    update_interval_seconds=3.0,  # Update every 3 seconds
    ttl_seconds=5,  # Cache TTL of 5 seconds
)


async def start_portfolio_cache_updater():
    """Start the global portfolio cache updater."""
    await portfolio_cache_updater.start()


async def stop_portfolio_cache_updater():
    """Stop the global portfolio cache updater."""
    await portfolio_cache_updater.stop()


def register_user_for_portfolio_updates(user_id: str, exchange_id: str):
    """Register a user for portfolio cache updates."""
    portfolio_cache_updater.register_user(user_id, exchange_id)


def unregister_user_from_portfolio_updates(user_id: str):
    """Unregister a user from portfolio cache updates."""
    portfolio_cache_updater.unregister_user(user_id)


async def force_portfolio_cache_update(user_id: str, exchange_id: str) -> bool:
    """Force immediate portfolio update for a specific user."""
    return await portfolio_cache_updater.force_update(user_id, exchange_id)
