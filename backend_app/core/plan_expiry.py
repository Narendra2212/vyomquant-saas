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
import re
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
            # BILL-09: this one matters more than the loop's, because it fires
            # per user. A plan that failed to expire is a customer still holding
            # paid entitlements they have stopped paying for, and the old log
            # line could not say why.
            logger.error(
                "[BILLING] BILL-01: could not expire plan for %s: %s: %s",
                uid, type(exc).__name__, _describe(exc), exc_info=True,
            )
    return expired


def _describe(exc: BaseException) -> str:
    """An exception rendered with enough detail to diagnose, and no credential.

    BILL-09. Both handlers below logged `type(exc).__name__` and nothing else.
    That is why 304 production failures between 2026-11-15 and 2026-11-17
    produced exactly one string -- "APIError" -- and no way to tell what had
    gone wrong. `APIError` is PostgREST's wrapper: the cause is in its
    `message`/`code`/`hint`/`details`, all of which were being thrown away.

    Those four fields are safe to log. They are the server's own account of a
    rejected statement -- "column x does not exist", "permission denied for
    table y" -- and carry no DSN. `str(exc)` is NOT used as the primary source
    because a psycopg2 OperationalError's text can contain the full connection
    string, host and user included; it is used only as a fallback and is
    truncated. Same reasoning as `core/readiness.py::_safe_detail`.
    """
    parts = []
    for attr in ("message", "code", "hint", "details"):
        value = getattr(exc, attr, None)
        if value:
            parts.append(f"{attr}={value}")
    if parts:
        return " ".join(parts)[:400]
    # Nothing structured. Fall back to the text, but keep it short and drop
    # anything that looks like a URI, which is where a credential would sit.
    text = re.sub(r"\b\w+://\S+", "<uri-redacted>", str(exc))
    return text[:300] or "(no detail)"


async def run_plan_expiry_loop(get_client, interval: int = SWEEP_INTERVAL_SECONDS) -> None:
    """Forever: sweep, sleep. Never dies on a failed sweep; cancelled on shutdown.

    The swallow is deliberate and stays: a failed sweep must not kill the loop,
    because the next hour's sweep is what recovers from a transient database
    problem. What changed under BILL-09 is that the failure is now reported in
    enough detail to act on.
    """
    while True:
        try:
            client = get_client()
            if client is not None:
                await expire_lapsed_one_time_plans(client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[BILLING] plan-expiry sweep failed: %s: %s",
                type(exc).__name__, _describe(exc), exc_info=True,
            )
        await asyncio.sleep(interval)
