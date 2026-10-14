"""backend/deployment_state.py - RT-08: durable deployment state the runtime writes and reads.

THE DEFECT THIS CLOSES
    A bot lives in one process's memory. Three things followed from that:

    * A bot that crashed, failed to boot or gave up reconnecting left its
      ``strategy_deployments`` row at ``running`` -- the fleet logged "exited cleanly" and wrote
      nothing, so the UI and the quota kept describing a bot that no longer existed.
    * ``stop_deployment`` only reached a bot in the SAME process (and passed the exchange id
      where the symbol belongs, so it missed even then); on any other pod the request was
      reported as success while the bot kept trading.
    * A restart lost every bot, and nothing started them again.

WHAT THIS MODULE IS
    The runtime's side of the deployment row. It is deliberately tiny and takes the Supabase
    client as a dependency so it can be exercised without a database. It runs under the SERVICE
    role (background work has no request JWT), and every write is CONDITIONED on the current
    status, so a late runtime report can never overwrite a user's stop and a stop can never be
    overwritten by a stale "failed".

STATES (``strategy_deployments.status``, free text; no CHECK constraint exists)
    deploying -> running -> stopping -> stopped      (user stop: owner confirms, then 'stopped')
    deploying | running -> failed                    (runtime reports the reason in error_message)
    A 'stopping' row nobody owns is finalised to 'stopped' by :func:`resume_deployments`.

Regression: tests/test_rt08_deployment_state_is_durable.py
"""
from __future__ import annotations

import inspect
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("DeploymentState")

LIVE_STATES = ("deploying", "running")
STOP_STATES = ("stopping", "stopped")


async def _run(query: Any) -> Any:
    result = query.execute()
    return await result if inspect.isawaitable(result) else result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeploymentStateStore:
    """Reads and conditional writes of one deployment row, as the platform."""

    def __init__(self, client_factory: Optional[Callable[[], Any]] = None) -> None:
        self._client_factory = client_factory

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        from backend_app.core.dependencies import get_service_supabase

        return get_service_supabase()

    # ── reads ───────────────────────────────────────────────────────────

    async def status_of(self, deployment_id: str) -> Optional[str]:
        """The row's status, or ``None`` when it cannot be read (unknown is not 'stop')."""
        try:
            sb = self._client()
            res = await _run(
                sb.table("strategy_deployments").select("status").eq("id", deployment_id)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("deployment %s status unreadable: %s", deployment_id, exc)
            return None
        rows = getattr(res, "data", None) or []
        return str(rows[0].get("status")) if rows else None

    async def rows_in(self, statuses: tuple) -> List[Dict[str, Any]]:
        sb = self._client()
        res = await _run(
            sb.table("strategy_deployments").select("*").in_("status", list(statuses))
        )
        return list(getattr(res, "data", None) or [])

    # ── conditional writes ──────────────────────────────────────────────

    async def mark_failed(self, deployment_id: str, reason: str) -> bool:
        """``deploying|running -> failed``. False when the row was not in a live state."""
        try:
            sb = self._client()
            res = await _run(
                sb.table("strategy_deployments")
                .update({"status": "failed", "error_message": str(reason)[:1000],
                         "stopped_at": _now()})
                .eq("id", deployment_id)
                .in_("status", list(LIVE_STATES))
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("could not record failure of deployment %s: %s", deployment_id, exc)
            return False
        changed = bool(getattr(res, "data", None))
        if changed:
            await self._release_quota(deployment_id)
        return changed

    async def mark_stopped(self, deployment_id: str) -> bool:
        """``running|deploying|stopping -> stopped``."""
        try:
            sb = self._client()
            res = await _run(
                sb.table("strategy_deployments")
                .update({"status": "stopped", "stopped_at": _now()})
                .eq("id", deployment_id)
                .in_("status", [*LIVE_STATES, "stopping"])
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("could not record stop of deployment %s: %s", deployment_id, exc)
            return False
        return bool(getattr(res, "data", None))

    async def request_stop(self, deployment_id: str) -> bool:
        """``running|deploying -> stopping``: the durable stop request the owner honours."""
        sb = self._client()
        res = await _run(
            sb.table("strategy_deployments")
            .update({"status": "stopping"})
            .eq("id", deployment_id)
            .in_("status", list(LIVE_STATES))
        )
        return bool(getattr(res, "data", None))

    async def _release_quota(self, deployment_id: str) -> None:
        """A failed deployment no longer occupies a bot slot."""
        try:
            sb = self._client()
            res = await _run(
                sb.table("strategy_deployments").select("user_id").eq("id", deployment_id)
            )
            rows = getattr(res, "data", None) or []
            if not rows:
                return
            from backend_app.core.subscription_engine import Resource, SubscriptionEngine

            await SubscriptionEngine.decrement_quota_usage(
                str(rows[0]["user_id"]), Resource.BOTS.value
            )
        except Exception as exc:  # noqa: BLE001 - best effort; the failure is already recorded
            logger.warning("quota not released for failed deployment %s: %s", deployment_id, exc)
