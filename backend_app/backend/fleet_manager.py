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
import time
from typing import Any, Dict, Optional, Tuple

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
        self, user_id: str, symbol: str, blueprint: dict, *, paper_trading: bool
    ) -> Tuple[bool, str]:
        """Spawn one bot.

        ``paper_trading`` is REQUIRED and keyword-only. It is the deployment's resolved
        mode, decided by the caller that evaluated the binding, and it is what decides
        whether this bot can reach a real exchange. There is deliberately no default: the
        defect this parameter exists to close was `BotRunner` reading the mode from a
        blueprint key nothing wrote and defaulting it to paper, so every live deployment was
        recorded as live and run as paper.

        Regression: tests/test_live_deployment_mode_propagation.py
        """
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

            # ═══════════════════════════════════════════════════════════════
            # WORK-03: THE CROSS-PROCESS SINGLE-OWNER CLAIM
            # ═══════════════════════════════════════════════════════════════
            # The `bot_key in self._active_fleet` check above is a per-PROCESS dict, so
            # two pods cannot see each other's bots and each will start its own BotRunner
            # for the same (user, symbol). The WORK-02 XAUTOCLAIM reaper made that
            # reachable rather than theoretical: a reclaimed `start_bot` command is
            # delivered to a DIFFERENT consumer than the one that first took it.
            #
            # `claim_bot` takes a PostgreSQL claim whose PRIMARY KEY (user_id, symbol) IS
            # the invariant -- `INSERT ... ON CONFLICT DO UPDATE ... WHERE heartbeat_at <
            # now() - threshold`, so of two concurrent claimants exactly one matches and
            # the loser gets zero rows. It FAILS CLOSED: an unreachable store refuses the
            # start, because a claim we cannot establish is indistinguishable from one
            # somebody else holds.
            #
            # Taken AFTER the cheap local checks so a same-process duplicate short
            # circuits without a round trip, and BEFORE the BotRunner is constructed so
            # no bot exists if the claim is refused.
            # Regression: tests/test_work03_bot_ownership_claims_postgres.py
            from backend_app.core.bot_ownership import claim_bot, release_bot

            ownership = claim_bot(user_id, symbol)
            if not ownership.granted:
                logger.warning(
                    "Refusing to start %s: %s (worker=%s)",
                    bot_key, ownership.outcome, ownership.worker_name,
                )
                return False, (
                    f"Another process already owns the bot for {symbol} "
                    f"({ownership.outcome})."
                )

            try:
                bot = BotRunner(
                    user_id=user_id,
                    symbol=symbol,
                    strategy_blueprint=blueprint,
                    global_state=app_state,
                    paper_trading=paper_trading,
                )
            except Exception as e:
                logger.error(f"Bot construction failed [{bot_key}]: {e}")
                # WORK-03: give the claim back. Without this a construction failure
                # strands the bot for a full stale interval -- the trader retries, is
                # told "another process owns it", and nothing owns it at all.
                release_bot(ownership)
                return False, f"Bot initialisation failed: {e}"

            # FIX 1: Register the bot IMMEDIATELY so HTTP handler returns at once.
            # _recover_live_state() runs in the background task — no Gunicorn timeout.
            task = asyncio.create_task(
                self._boot_and_run(bot, bot_key), name=bot_key
            )
            task.add_done_callback(
                lambda t: asyncio.create_task(self._cleanup_dead_bot(bot_key, t))
            )

            # WORK-03: keep the claim fresh for as long as the bot runs. WITHOUT THIS the
            # claim goes stale after the threshold while the bot is still trading, and
            # another process is then entitled to take ownership and start a SECOND
            # runner for the same (user, symbol) -- the defect WORK-03 exists to prevent,
            # merely postponed by 90 seconds rather than prevented.
            heartbeat_task = asyncio.create_task(
                self._heartbeat_claim(bot_key, ownership), name=f"{bot_key}:claim-hb"
            )

            # FIX FM-1: Store the original slash-format symbol for clean shutdown
            # WORK-03: the ownership claim travels with the fleet record so stop_bot and
            # _cleanup_dead_bot can release it. A claim that is never released is a
            # stranded bot until the stale threshold expires.
            self._active_fleet[bot_key] = {
                "task": task, "bot": bot, "symbol": symbol, "claim": ownership,
                "heartbeat": heartbeat_task,
            }
            logger.info(f"Bot scheduled: {bot_key} (initializing in background)")

        return True, symbol

    # ═══════════════════════════════════════════════════════════════════════
    # WORK-03: claim liveness
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _cancel_claim_heartbeat(record: Optional[Dict[str, Any]]) -> None:
        """Stop the heartbeat task for a fleet record. Safe on a finished task."""
        if not record:
            return
        task = record.get("heartbeat")
        if task is not None and not task.done():
            task.cancel()

    async def _surrender_bot(self, bot_key: str, reason: str) -> None:
        """Stop the local runner because this process no longer owns the bot.

        Deliberately does NOT release the claim. Release is scoped to our `owner_token`,
        so the DELETE would match zero rows anyway -- and were it not scoped, it would
        delete the NEW owner's claim and let a third worker start yet another bot.
        Cancelling the task is sufficient; the done-callback performs the (no-op) release.
        """
        logger.critical("WORK-03: surrendering %s — %s.", bot_key, reason)
        async with self._lock:
            record = self._active_fleet.get(bot_key)
        if record is not None:
            record["task"].cancel()

    async def _heartbeat_claim(self, bot_key: str, claim) -> None:
        """Refresh this process's ownership claim, and stop the bot if it is ever lost.

        Two failure modes, handled oppositely on purpose:

        * **Ownership lost** (the scoped UPDATE matched zero rows) -- another worker owns
          this bot now. Stop trading at once; continuing is the two-runner bug.
        * **Store unreachable** -- we do not know. A transient blip must not kill a live
          bot, so we keep running. But it must not grant indefinite ownership either:
          once we have been unable to refresh for longer than the stale threshold,
          another worker is *entitled* to take over, so we must assume one has and stand
          down. Fail-closed on the only axis that can produce duplicate venue writes.
        """
        from backend_app.core.bot_ownership import (
            ClaimStoreUnavailable,
            HEARTBEAT_INTERVAL_SECONDS,
            effective_stale_seconds,
            heartbeat_bot,
        )

        unreachable_since: Optional[float] = None
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            try:
                # psycopg2 is blocking. Calling it directly would stall the event loop
                # that every other bot in this process is running on.
                still_ours = await asyncio.to_thread(heartbeat_bot, claim)
            except asyncio.CancelledError:
                raise
            except ClaimStoreUnavailable as exc:
                now = time.monotonic()
                if unreachable_since is None:
                    unreachable_since = now
                    logger.warning(
                        "WORK-03: cannot refresh the claim on %s (%s). The bot keeps "
                        "running for up to %ss; after that another worker may own it.",
                        bot_key, exc, effective_stale_seconds(),
                    )
                elif now - unreachable_since > effective_stale_seconds():
                    await self._surrender_bot(
                        bot_key,
                        f"claim store unreachable for more than "
                        f"{effective_stale_seconds()}s, so ownership can no longer be "
                        f"established",
                    )
                    return
                continue
            except Exception as exc:  # noqa: BLE001
                # An unexpected error here must not silently end the heartbeat loop and
                # leave the bot running on a claim nobody is refreshing.
                logger.error(
                    "WORK-03: unexpected heartbeat error for %s (%s); treating it as "
                    "store-unreachable.", bot_key, exc,
                )
                if unreachable_since is None:
                    unreachable_since = time.monotonic()
                continue

            unreachable_since = None
            if not still_ours:
                await self._surrender_bot(
                    bot_key, "the ownership claim was taken over by another worker"
                )
                return

    async def _boot_and_run(self, bot, bot_key: str):
        """Background task: initialize the bot then enter the trading loop.
        FIX 1: Runs outside the HTTP request so Gunicorn timeout cannot kill it."""
        try:
            await bot._recover_live_state()
            logger.info(f"Bot ready: {bot_key}")
        except Exception as e:
            logger.error(f"Bot boot failed [{bot_key}]: {e}")
            async with self._lock:
                record = self._active_fleet.pop(bot_key, None)
            # WORK-03: this branch pops the record itself, so the done-callback that
            # normally releases the claim finds nothing and the claim LEAKS -- stranding
            # the bot for a full stale interval after a boot failure. Release here.
            if record is not None:
                from backend_app.core.bot_ownership import release_bot
                self._cancel_claim_heartbeat(record)
                release_bot(record.get("claim"))
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

        # WORK-03: release the cross-process claim so the bot can be restarted
        # immediately rather than after the stale threshold. The heartbeat task is
        # cancelled FIRST, so it cannot refresh a claim we are about to delete.
        from backend_app.core.bot_ownership import release_bot
        self._cancel_claim_heartbeat(fleet_record)
        release_bot(fleet_record.get("claim"))

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
            record = self._active_fleet.pop(bot_key, None)  # no-op if already removed

        # WORK-03: a bot that CRASHED must also give up its claim, or the restart is
        # refused for a full stale interval. `stop_bot` already popped the record in the
        # clean path, so `record` is None there and this is a no-op.
        if record is not None:
            from backend_app.core.bot_ownership import release_bot
            self._cancel_claim_heartbeat(record)
            release_bot(record.get("claim"))

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
