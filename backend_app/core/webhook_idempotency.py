"""
core/webhook_idempotency.py — Durable webhook delivery idempotency (SEC-03).

THE DEFECT
----------
backend_app/routers/billing.py's Stripe and Razorpay webhook handlers tracked
delivery idempotency ENTIRELY in Redis: a "processing" key (60s TTL, to avoid a
crashed delivery blocking retries for a day) that transitioned to "completed"
(86400s TTL) on success. core/cache/redis_manager.py is deliberately fail-soft
-- every method swallows its own exception and returns a falsy default -- so a
Redis outage during the initial check silently read as "not a duplicate" and the
handler proceeded to reprocess a REAL duplicate delivery undetected. And even with
Redis fully healthy, a redelivery more than 24 hours after the first (both Stripe
and Razorpay redeliver for days, and an operator can manually resend an old webhook
from either dashboard for reconciliation) found no record at all once the
"completed" key had expired, and was processed as if new.

THE FIX
-------
public.webhook_events (migration 045) is now the authoritative idempotency record:
UNIQUE (provider, event_id), with a durable 'processing' / 'completed' row per
delivery that never expires. Postgres unique-constraint enforcement replaces
Redis's SETNX as the atomicity primitive, and because it is a real constraint
rather than a cache, "the record is gone" is no longer a possible state -- there is
no TTL to outlive and no fail-soft client to silently read an outage as "go ahead".

The crash-safety property the Redis version had is preserved: a 'processing' row
older than PROCESSING_STALE_AFTER_SECONDS is treated as abandoned (the process that
claimed it crashed or hung) and may be reclaimed by a later delivery, so one crash
cannot block every future retry forever.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("WebhookIdempotency")

#: A 'processing' row older than this is assumed abandoned (the delivery that
#: claimed it crashed, or hung past any plausible handler runtime) and may be
#: reclaimed by a later redelivery attempt -- mirrors the previous Redis
#: implementation's 60s "processing" TTL.
PROCESSING_STALE_AFTER_SECONDS = 60


async def _client():
    from backend_app.core.dependencies import get_service_supabase_async

    return await get_service_supabase_async()


def _is_unique_violation(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    if code == "23505":
        return True
    text = str(exc)
    return "23505" in text or "duplicate key" in text.lower()


def _parse_timestamp(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


async def acquire_webhook_processing(provider: str, event_id: str) -> bool:
    """Claim this (provider, event_id) for processing.

    Returns True if this delivery should proceed (first attempt, or a reclaim of a
    stale, abandoned attempt). Returns False if another delivery already completed
    it, or is actively processing it and not yet stale -- a duplicate.

    This is a durability layer UNDERNEATH billing.py's own Redis-based check, not a
    replacement for it (see that module's comments at each call site) -- so the two
    failure modes below differ in direction on purpose:

    * NOT CONFIGURED (``get_service_supabase_async()`` returns ``None``: no
      SUPABASE_SERVICE_ROLE_KEY, the ordinary shape of a local/dev/test environment)
      -- there is nothing durable to check, so this returns True (defer entirely to
      Redis, exactly the behaviour before this layer existed). Refusing here would
      turn "this optional hardening is not configured" into "every webhook is a
      duplicate", which is not this layer's call to make.
    * CONFIGURED BUT UNREACHABLE (a client exists but the call itself raises --
      production has credentials but Postgres/PostgREST errors or times out) -- fails
      CLOSED, returning False, rather than silently permitting a delivery whose
      idempotency cannot be verified. A webhook provider retries on a non-2xx
      response, so refusing here is recoverable; double-processing a payment is not.
    """
    sb = await _client()
    if sb is None:
        return True

    try:
        await sb.table("webhook_events").insert(
            {"provider": provider, "event_id": event_id, "status": "processing"}
        ).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        if not _is_unique_violation(exc):
            logger.error(
                "[WebhookIdempotency] acquire failed for %s:%s: %s", provider, event_id, exc,
            )
            return False

    # A row already exists for this delivery. Decide whether it is a live
    # duplicate, a completed delivery, or an abandoned attempt we may reclaim.
    try:
        resp = (
            await sb.table("webhook_events")
            .select("status, created_at")
            .eq("provider", provider)
            .eq("event_id", event_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[WebhookIdempotency] post-conflict read failed for %s:%s: %s",
            provider, event_id, exc,
        )
        return False

    rows = resp.data or []
    if not rows:
        # Vanished between the conflict and this read (another process released
        # it). Safe to try the claim once more.
        try:
            await sb.table("webhook_events").insert(
                {"provider": provider, "event_id": event_id, "status": "processing"}
            ).execute()
            return True
        except Exception:  # noqa: BLE001
            return False

    row = rows[0]
    if row.get("status") != "processing":
        return False  # already completed

    created_at = _parse_timestamp(row.get("created_at"))
    now = datetime.now(timezone.utc)
    if created_at is None or now - created_at < timedelta(seconds=PROCESSING_STALE_AFTER_SECONDS):
        return False  # a live attempt, not yet stale

    # Reclaim: the WHERE clause (still 'processing' AND still older than the
    # staleness cutoff) is evaluated server-side in one statement, so a
    # concurrent reclaim by another process cannot be doubly won.
    cutoff = (now - timedelta(seconds=PROCESSING_STALE_AFTER_SECONDS)).isoformat()
    try:
        reclaim = (
            await sb.table("webhook_events")
            .update({"status": "processing", "created_at": now.isoformat()})
            .eq("provider", provider)
            .eq("event_id", event_id)
            .eq("status", "processing")
            .lt("created_at", cutoff)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[WebhookIdempotency] reclaim failed for %s:%s: %s", provider, event_id, exc,
        )
        return False

    return bool(reclaim.data)


async def mark_webhook_completed(provider: str, event_id: str) -> None:
    """Record a delivery as durably completed.

    Never raises -- a failure here must not turn a successfully-processed webhook
    into a 500, which would make the provider redeliver a payment that already
    succeeded.
    """
    sb = await _client()
    if sb is None:
        return
    try:
        await sb.table("webhook_events").update(
            {"status": "completed", "completed_at": datetime.now(timezone.utc).isoformat()}
        ).eq("provider", provider).eq("event_id", event_id).execute()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[WebhookIdempotency] mark-completed failed for %s:%s: %s", provider, event_id, exc,
        )


async def release_webhook_processing(provider: str, event_id: str) -> None:
    """Release a claim after a failed attempt, so the next redelivery can proceed
    immediately rather than waiting out PROCESSING_STALE_AFTER_SECONDS.

    Never raises, for the same reason as mark_webhook_completed.
    """
    sb = await _client()
    if sb is None:
        return
    try:
        await sb.table("webhook_events").delete().eq("provider", provider).eq(
            "event_id", event_id
        ).eq("status", "processing").execute()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[WebhookIdempotency] release failed for %s:%s: %s", provider, event_id, exc,
        )
