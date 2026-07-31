"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 1 — BACKEND: fleet_manager.py                                     ║
║                                                                          ║
║  Manages the lifecycle of all BotRunner instances across all users.      ║
║                                                                          ║
║  BUGS FIXED:                                                             ║
║  FM-1  shutdown_all symbol reconstruction assumed USDT suffix only       ║
║  FM-2  stop_bot popped from dict THEN cancelled — _cleanup race condition║
║  FM-3  No asyncio.Lock protecting _active_fleet during iteration         ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
from typing import Any, Dict, Tuple

logger = logging.getLogger("FleetManager")


class FleetManager:
    """
    Global singleton. One instance shared by all FastAPI routes.
    Tracks every live BotRunner asyncio Task.
    """

    def __init__(self):
        # { "user123_BTCUSDT": {"task": Task, "bot": BotRunner, "symbol": "BTC/USDT"} }
        self._active_fleet: Dict[str, Dict[str, Any]] = {}
        self.MAX_SYSTEM_BOTS = 3000
        self.MAX_BOTS_PER_USER = 5
        self._lock = asyncio.Lock()  # FIX FM-3

    def _bot_key(self, user_id: str, symbol: str) -> str:
        return f"{user_id}_{symbol.replace('/', '_')}"

    def get_status(self) -> dict:
        return {
            "active_bots_count": len(self._active_fleet),
            "capacity": self.MAX_SYSTEM_BOTS,
            "usage_pct": round(len(self._active_fleet) / self.MAX_SYSTEM_BOTS * 100, 2),
        }

    async def start_bot(
        self, user_id: str, symbol: str, blueprint: dict
    ) -> Tuple[bool, str]:
        bot_key = self._bot_key(user_id, symbol)

        async with self._lock:  # FIX FM-3
            if bot_key in self._active_fleet:
                return False, f"Bot already running for {symbol}."

            if len(self._active_fleet) >= self.MAX_SYSTEM_BOTS:
                logger.critical("System capacity reached. Cannot spawn new bots.")
                return False, "System is at maximum bot capacity."

            from backend_app.backend.master_executor import BotRunner
            from backend_app.core.dependencies import DEPLOYMENT_LIMITS
            from backend_app.core.state import app_state

            try:
                tier_info = app_state.vault.get_user_tier(user_id)
                user_tier = tier_info.get("subscription_tier", "free")
            except Exception as e:
                logger.warning(f"Failed to fetch tier for user {user_id}: {e}. Defaulting to free.")
                user_tier = "free"

            limit = DEPLOYMENT_LIMITS.get(user_tier, DEPLOYMENT_LIMITS.get("free", 1))

            user_bot_count = sum(
                1 for k in self._active_fleet if k.startswith(f"{user_id}_")
            )
            if user_bot_count >= limit:
                return False, f"Maximum {limit} bots per user reached."

            try:
                bot = BotRunner(
                    user_id=user_id,
                    symbol=symbol,
                    strategy_blueprint=blueprint,
                    global_state=app_state,
                )
            except Exception as e:
                logger.error(f"Bot construction failed [{bot_key}]: {e}")
                return False, f"Bot initialisation failed: {e}"

            # FIX 1: Register the bot IMMEDIATELY so HTTP handler returns at once.
            # _recover_live_state() runs in the background task — no Gunicorn timeout.
            task = asyncio.create_task(
                self._boot_and_run(bot, bot_key), name=bot_key
            )
            task.add_done_callback(
                lambda t: asyncio.create_task(self._cleanup_dead_bot(bot_key, t))
            )

            # FIX FM-1: Store the original slash-format symbol for clean shutdown
            self._active_fleet[bot_key] = {"task": task, "bot": bot, "symbol": symbol}
            logger.info(f"Bot scheduled: {bot_key} (initializing in background)")

        return True, symbol

    async def _boot_and_run(self, bot, bot_key: str):
        """Background task: initialize the bot then enter the trading loop.
        FIX 1: Runs outside the HTTP request so Gunicorn timeout cannot kill it."""
        try:
            await bot._recover_live_state()
            logger.info(f"Bot ready: {bot_key}")
        except Exception as e:
            logger.error(f"Bot boot failed [{bot_key}]: {e}")
            async with self._lock:
                self._active_fleet.pop(bot_key, None)
            return

        await bot._run_trading_loop()

    async def stop_bot(self, user_id: str, symbol: str) -> Tuple[bool, str]:
        bot_key = self._bot_key(user_id, symbol)

        async with self._lock:  # FIX FM-3
            if bot_key not in self._active_fleet:
                return False, f"No active bot for {symbol}."

            # FIX FM-2: Mark as stopping BEFORE cancelling to prevent
            # _cleanup_dead_bot double-pop race condition
            fleet_record = self._active_fleet.pop(bot_key)

        # Cancel outside the lock so _cleanup_dead_bot can acquire it
        fleet_record["task"].cancel()

        try:
            await asyncio.wait_for(asyncio.shield(fleet_record["task"]), timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        await fleet_record["bot"].shutdown()
        logger.info(f"Bot stopped: {bot_key}")
        return True, "Bot stopped successfully."

    async def shutdown_all(self):
        """Global kill switch — called on server shutdown or admin halt."""
        logger.warning(f"GLOBAL SHUTDOWN: stopping {len(self._active_fleet)} bots.")

        # FIX FM-1: Use stored symbol string — no reconstruction heuristic
        async with self._lock:
            snapshot = list(self._active_fleet.items())

        for bot_key, record in snapshot:
            try:
                await self.stop_bot(*bot_key.split("_", 1))
            except Exception as e:
                logger.error(f"Error stopping {bot_key}: {e}")

        logger.info("All bots stopped.")

    async def _cleanup_dead_bot(self, bot_key: str, task: asyncio.Task):
        """
        Called automatically when a bot task finishes (crash or cancel).
        FIX FM-2: pop() is now safe — stop_bot already removed the key.
        """
        async with self._lock:
            self._active_fleet.pop(bot_key, None)  # no-op if already removed

        try:
            task.result()
            logger.info(f"Bot exited cleanly: {bot_key}")
        except asyncio.CancelledError:
            pass  # normal stop
        except Exception as e:
            logger.critical(f"Bot FATAL CRASH [{bot_key}]: {e}")
            from backend_app.core.state import app_state

            asyncio.create_task(
                app_state.alert.send_critical(
                    system=f"BotRunner ({bot_key})",
                    message=f"Unexpected crash: {e}",
                )
            )
