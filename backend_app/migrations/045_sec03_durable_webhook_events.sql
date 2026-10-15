-- backend_app/migrations/045_sec03_durable_webhook_events.sql
--
-- SEC-03 (P2), the "webhook idempotency lives only in Redis for 24h" half. (The
-- "refunds do not revoke plan entitlement" half was already fixed by BILL-01's
-- _revoke_plan_for_refunded_razorpay_payment, VERIFIED_FIXED earlier this session --
-- see that finding's ledger entry.)
--
-- THE DEFECT. backend_app/routers/billing.py's Stripe and Razorpay webhook handlers
-- tracked delivery idempotency entirely in Redis: a "processing" key (60s TTL, so a
-- crashed delivery does not block retries for a full day) that transitioned to
-- "completed" (86400s TTL) on success. core/cache/redis_manager.py is deliberately
-- fail-soft (every method swallows its own exception and returns a falsy default), so
-- a Redis outage during the initial idempotency check silently read as "not a
-- duplicate" and the handler proceeded to reprocess a real duplicate delivery
-- undetected. And even with Redis fully healthy, any redelivery more than 24 hours
-- after the first (both providers redeliver for days on a non-2xx response, and an
-- operator can manually resend an old webhook from either dashboard for
-- reconciliation) found no record at all once the "completed" key expired, and was
-- processed as new -- a second checkout.session.completed could grant a plan twice, a
-- second refund.processed could revoke it twice.
--
-- THE FIX. This table is the new, durable, authoritative idempotency record:
-- UNIQUE (provider, event_id), with a 'processing' / 'completed' row per delivery that
-- never expires. Postgres's own unique-constraint enforcement replaces Redis's SETNX
-- as the atomicity primitive -- there is no TTL for the record to outlive, and no
-- fail-soft client that can silently read an outage as "go ahead". A 'processing' row
-- older than 60 seconds is still treated as an abandoned attempt and may be reclaimed
-- by a later redelivery (core/webhook_idempotency.py's PROCESSING_STALE_AFTER_SECONDS),
-- preserving the crash-safety property the Redis version had.
--
-- NOT append-only, unlike 036_order_audit_log_durable.sql: that table's whole point is
-- that a recorded decision can never be edited. This one's whole point is the opposite
-- -- a 'processing' row is deliberately mutated to 'completed', or deleted on a failed
-- attempt so the next redelivery is not forced to wait out the staleness window.
--
-- RLS is enabled with no policy and every privilege is revoked from anon/authenticated:
-- only the backend (service_role) ever reads or writes it -- this is platform
-- bookkeeping, never a per-user row. Idempotent and additive; touches no existing
-- table.

CREATE TABLE IF NOT EXISTS public.webhook_events (
    id              bigserial PRIMARY KEY,
    provider        text NOT NULL,
    event_id        text NOT NULL,
    status          text NOT NULL DEFAULT 'processing',
    created_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    CONSTRAINT uq_webhook_events_provider_event UNIQUE (provider, event_id),
    CONSTRAINT chk_webhook_events_status CHECK (status IN ('processing', 'completed'))
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_created_at
    ON public.webhook_events (status, created_at)
    WHERE status = 'processing';

ALTER TABLE public.webhook_events ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON public.webhook_events FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON public.webhook_events FROM authenticated';
    END IF;
END
$$;

-- Postflight: say what landed, and refuse clearly if it did not.
DO $$
BEGIN
    IF to_regclass('public.webhook_events') IS NULL THEN
        RAISE EXCEPTION '045: public.webhook_events was not created.';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_webhook_events_provider_event'
    ) THEN
        RAISE EXCEPTION '045: uq_webhook_events_provider_event was not created.';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
       AND has_table_privilege('anon', 'public.webhook_events', 'SELECT') THEN
        RAISE EXCEPTION '045: anon can still read public.webhook_events.';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated')
       AND has_table_privilege('authenticated', 'public.webhook_events', 'SELECT') THEN
        RAISE EXCEPTION '045: authenticated can still read public.webhook_events.';
    END IF;
    RAISE NOTICE '045: public.webhook_events present, service-role only, (provider, '
                 'event_id) unique -- webhook idempotency is now durable.';
END
$$;
