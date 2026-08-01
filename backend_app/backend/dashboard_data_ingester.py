"""
backend/dashboard_data_ingester.py — Dashboard Data Ingestion Service

Background service that populates QuestDB tables with real portfolio data for Dashboard widgets.

Tables populated:
- equity_curve: Historical portfolio equity values
- account_health: Risk metrics (drawdown, daily PnL, exposure)
- executions: Trade execution records (already populated by TelemetryEngine.log_execution)

Data sources:
- Redis portfolio cache (populated by PortfolioCacheUpdater)
- Exchange API (via ConnectionEngine)
- Calculated metrics (drawdown, PnL)

Frequency: Every 60 seconds (configurable)
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger(__name__)


class DashboardDataIngester:
    """
    Background service that ingests portfolio data into QuestDB for Dashboard widgets.
    
    This service:
    1. Reads portfolio data from Redis cache (populated by PortfolioCacheUpdater)
    2. Calculates equity curve points
    3. Calculates account health metrics
    4. Writes data to QuestDB tables
    """
    
    def __init__(self, update_interval_seconds: int = 60):
        self.update_interval = update_interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
    async def start(self):
        """Start the background ingester."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._update_loop())
        logger.info(f"✅ DashboardDataIngester started (interval: {self.update_interval}s)")
        
    async def stop(self):
        """Stop the background ingester."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 DashboardDataIngester stopped")
    
    async def _update_loop(self):
        """Main update loop - runs periodically to ingest data."""
        while self._running:
            try:
                await self._ingest_all_users()
                await asyncio.sleep(self.update_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Dashboard data ingestion error: {e}")
                await asyncio.sleep(5)  # Brief pause on error
    
    async def _ingest_all_users(self):
        """Ingest data for all registered users."""
        from backend_app.core.state import app_state
        
        # Get list of users from PortfolioCacheUpdater
        # For now, we'll iterate through registered users in the cache updater
        if hasattr(app_state, 'portfolio_cache_updater'):
            user_exchanges = app_state.portfolio_cache_updater._user_exchanges
        else:
            logger.warning("PortfolioCacheUpdater not available for user list")
            return
        
        for user_id, exchange_id in user_exchanges.items():
            try:
                await self._ingest_user_data(user_id, exchange_id)
            except Exception as e:
                logger.error(f"❌ Failed to ingest data for user {user_id}: {e}")
    
    async def _ingest_user_data(self, user_id: str, exchange_id: str):
        """
        Ingest portfolio data for a single user.
        
        Args:
            user_id: User identifier
            exchange_id: Exchange identifier (e.g., "binance")
        """
        from backend_app.core.state import app_state
        
        # Read portfolio data from Redis cache
        redis_client = await redis_manager.get_client()
        balance_key = f"portfolio:{user_id}:{exchange_id}:balance"
        position_key = f"portfolio:{user_id}:{exchange_id}:positions"
        
        try:
            # Get balance data
            balance_data = await redis_client.hgetall(balance_key)
            if not balance_data:
                logger.debug(f"No balance data found for {user_id}:{exchange_id}")
                return
            
            total_equity = float(balance_data.get("total", 0))
            available_cash = float(balance_data.get("available", 0))
            total_exposure = float(balance_data.get("exposure", 0))
            
            # Get positions data
            positions_json = await redis_client.get(position_key)
            positions = []
            if positions_json:
                import json
                positions = json.loads(positions_json)
            
            # Calculate unrealized PnL from positions
            unrealized_pnl = 0.0
            for pos in positions:
                unrealized_pnl += float(pos.get("unrealized_pnl", 0))
            
            # Write equity curve point
            await self._write_equity_curve(user_id, total_equity)
            
            # Write account health metrics
            await self._write_account_health(
                user_id,
                total_equity,
                unrealized_pnl,
                total_exposure
            )
            
            logger.debug(
                f"💾 Ingested dashboard data for {user_id}:{exchange_id} "
                f"(equity: {total_equity}, exposure: {total_exposure})"
            )
            
        except Exception as e:
            logger.error(f"❌ Failed to ingest data for {user_id}:{exchange_id}: {e}")
    
    async def _write_equity_curve(self, user_id: str, equity: float):
        """
        Write an equity curve point to QuestDB.
        
        Args:
            user_id: User identifier
            equity: Current portfolio equity value
        """
        from backend_app.core.state import app_state
        
        timestamp = datetime.utcnow().isoformat()
        
        # Use ILP format for high-speed write
        # Format: equity_curve,user_id=xxx equity=xxx timestamp
        line = f"equity_curve,user_id={user_id} equity={equity} {int(datetime.utcnow().timestamp() * 1_000_000_000)}"
        
        try:
            session = await app_state.telemetry._get_session()
            async with session.post(app_state.telemetry.write_url, data=line) as response:
                if response.status == 204:
                    logger.debug(f"Equity curve point written for {user_id}")
                else:
                    logger.warning(f"Failed to write equity curve: HTTP {response.status}")
        except Exception as e:
            logger.error(f"Failed to write equity curve for {user_id}: {e}")
    
    async def _write_account_health(
        self,
        user_id: str,
        total_equity: float,
        unrealized_pnl: float,
        total_exposure: float
    ):
        """
        Write account health metrics to QuestDB.
        
        Args:
            user_id: User identifier
            total_equity: Current portfolio equity
            unrealized_pnl: Current unrealized PnL
            total_exposure: Total exposure in USDT
        """
        from backend_app.core.state import app_state
        
        # Calculate metrics
        current_drawdown_pct = 0.0
        daily_pnl_pct = 0.0
        
        if total_equity > 0:
            daily_pnl_pct = (unrealized_pnl / total_equity) * 100
        
        # For drawdown, we'd need historical data - for now use 0
        # In production, this would query the equity_curve table to calculate
        
        timestamp = datetime.utcnow().isoformat()
        
        # Use ILP format for high-speed write
        line = (
            f"account_health,user_id={user_id} "
            f"current_drawdown_pct={current_drawdown_pct},"
            f"daily_pnl_pct={daily_pnl_pct},"
            f"total_exposure_usdt={total_exposure} "
            f"{int(datetime.utcnow().timestamp() * 1_000_000_000)}"
        )
        
        try:
            session = await app_state.telemetry._get_session()
            async with session.post(app_state.telemetry.write_url, data=line) as response:
                if response.status == 204:
                    logger.debug(f"Account health written for {user_id}")
                else:
                    logger.warning(f"Failed to write account health: HTTP {response.status}")
        except Exception as e:
            logger.error(f"Failed to write account health for {user_id}: {e}")


# Global singleton instance
dashboard_data_ingester = DashboardDataIngester(update_interval_seconds=60)


async def start_dashboard_data_ingester():
    """Start the global dashboard data ingester."""
    await dashboard_data_ingester.start()


async def stop_dashboard_data_ingester():
    """Stop the global dashboard data ingester."""
    await dashboard_data_ingester.stop()
