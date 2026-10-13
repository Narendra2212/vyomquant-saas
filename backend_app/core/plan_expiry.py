"""BILL-01: persist the end of one-time (Razorpay) plan purchases.

``get_plan_context`` already stops honouring a lapsed tier on every request; this makes the
state true in the database as well (tier -> free, status -> expired) so reads that do not go
through the entitlement resolver, reports and the billing page agree.

Scope is deliberately narrow: only rows with ``payment_provider = 'razorpay'`` whose recorded
period has ended. Stripe customers are never touched here - Stripe's webhooks own their period.
Idempotent and safe to run from several workers: each update is conditional on the row still
being a lapsed Razorpay plan.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 3600
_BATCH = 500


async def expire_lapsed_one_time_plans(sb: Any, now: Optional[datetime] = None) -> List[str]:
    """Downgrade lapsed Razorpay plans to free. Returns the user ids that were expired."""
    now = now or datetime.now(timezone.utc)
    cutoff = now.isoformat()

    def _select():
        return (sb.table("profiles").select("id,subscription_tier,next_billing_date")
                .eq("payment_provider", "razorpay").neq("subscription_tier", "free")
                .lt("next_billing_date", cutoff).limit(_BATCH).execute())

    rows = (await asyncio.to_thread(_select)).data or []
    expired: List[str] = []
    for row in rows:
        uid = row["id"]

        def _expire(uid=uid):
            return (sb.table("profiles")
                    .update({"subscription_tier": "free", "subscription_status": "expired",
                             "cancel_at_period_end": False})
                    .eq("id", uid).eq("payment_provider", "razorpay")
                    .lt("next_billing_date", cutoff).execute())

        try:
            res = await asyncio.to_thread(_expire)
            if res.data:
                expired.append(uid)
                logger.info("[BILLING] BILL-01: plan %s expired for user %s (period ended %s)",
                            row.get("subscription_tier"), uid, row.get("next_billing_date"))
                try:
                    from backend_app.core.subscription_dependencies import invalidate_plan_cache
                    await invalidate_plan_cache(uid)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.error("[BILLING] BILL-01: could not expire plan for %s: %s", uid, type(exc).__name__)
    return expired


async def run_plan_expiry_loop(get_client, interval: int = SWEEP_INTERVAL_SECONDS) -> None:
    """Forever: sweep, sleep. Never dies on a failed sweep; cancelled on shutdown."""
    while True:
        try:
            client = get_client()
            if client is not None:
                await expire_lapsed_one_time_plans(client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("[BILLING] plan-expiry sweep failed: %s", type(exc).__name__)
        await asyncio.sleep(interval)
