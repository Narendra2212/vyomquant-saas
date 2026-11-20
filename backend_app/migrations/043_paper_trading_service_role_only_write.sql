-- backend_app/migrations/043_paper_trading_service_role_only_write.sql
--
-- PAPER-03 (P1). Migration 009's grants let `authenticated` write directly into the
-- paper-trading tables: SELECT, INSERT, UPDATE on `paper_sessions`, `paper_accounts`,
-- `paper_orders`, `paper_positions`, `paper_metrics`; SELECT, INSERT on `paper_fills`,
-- `paper_balance_events`, `paper_trades`, `paper_equity_snapshots`, `paper_events`,
-- `paper_market_events`. Paired with the `*_owner_access` RLS policies (009), which admit
-- any statement whose row is owned by `auth.uid()`, this let a trader's own JWT write
-- directly into their own paper-trading rows via a raw PostgREST call: fabricating a
-- balance, a fill price, a position size, or an order's state, bypassing every check
-- `PaperTradingService.place_order`/`cancel_order` perform (idempotency, risk limits, the
-- measured-price rule, the order-state transition guard).
--
-- Confirmed by reading the real application code
-- (backend/paper_trading_service.py::PaperTradingService._client, the sole Persistence_Layer
-- resolver every paper read and write goes through): it no longer ever builds a client from
-- the caller's own JWT. Every statement -- reads and writes alike -- now goes through the
-- platform's own service-role credential (`get_service_supabase()`), the same move PAPER-04
-- already made for the bot-driven session loop. The `authenticated` WRITE grant is therefore
-- not used by anything this platform does; it exists only as a surface a trader's own token
-- can still reach directly. SELECT is left untouched -- this finding, and its own remediation
-- note ("SELECT-own only for authenticated"), name the WRITE grant specifically, and the RLS
-- read policies continue to describe exactly the rows a direct read may still see.
--
-- `paper_order_allowed_transitions` and `paper_session_allowed_transitions` are untouched:
-- 009 only ever granted `authenticated` SELECT on them (they are seed/reference tables, not
-- per-user rows), so there is no WRITE grant on either to revoke.
--
-- Idempotent: REVOKE is idempotent by definition.

REVOKE INSERT, UPDATE ON public.paper_sessions  FROM authenticated;
REVOKE INSERT, UPDATE ON public.paper_accounts  FROM authenticated;
REVOKE INSERT, UPDATE ON public.paper_orders    FROM authenticated;
REVOKE INSERT, UPDATE ON public.paper_positions FROM authenticated;
REVOKE INSERT, UPDATE ON public.paper_metrics   FROM authenticated;

REVOKE INSERT ON public.paper_fills            FROM authenticated;
REVOKE INSERT ON public.paper_balance_events   FROM authenticated;
REVOKE INSERT ON public.paper_trades           FROM authenticated;
REVOKE INSERT ON public.paper_equity_snapshots FROM authenticated;
REVOKE INSERT ON public.paper_events           FROM authenticated;
REVOKE INSERT ON public.paper_market_events    FROM authenticated;

-- Postflight: authenticated can still SELECT every table (009's intent, unchanged) but can no
-- longer write any of them; service_role is unaffected.
DO $$
DECLARE
    rw_table text;
    ro_table text;
BEGIN
    FOREACH rw_table IN ARRAY ARRAY[
        'paper_sessions', 'paper_accounts', 'paper_orders', 'paper_positions', 'paper_metrics'
    ]
    LOOP
        IF has_table_privilege('authenticated', 'public.' || rw_table, 'INSERT') THEN
            RAISE EXCEPTION 'postflight failed: authenticated can still INSERT into %', rw_table;
        END IF;
        IF has_table_privilege('authenticated', 'public.' || rw_table, 'UPDATE') THEN
            RAISE EXCEPTION 'postflight failed: authenticated can still UPDATE %', rw_table;
        END IF;
        IF NOT has_table_privilege('authenticated', 'public.' || rw_table, 'SELECT') THEN
            RAISE EXCEPTION 'postflight failed: authenticated lost SELECT on %, which this migration does not intend to change', rw_table;
        END IF;
        IF NOT has_table_privilege('service_role', 'public.' || rw_table, 'INSERT') THEN
            RAISE EXCEPTION 'postflight failed: service_role lost INSERT on %', rw_table;
        END IF;
        IF NOT has_table_privilege('service_role', 'public.' || rw_table, 'UPDATE') THEN
            RAISE EXCEPTION 'postflight failed: service_role lost UPDATE on %', rw_table;
        END IF;
    END LOOP;

    FOREACH ro_table IN ARRAY ARRAY[
        'paper_fills', 'paper_balance_events', 'paper_trades', 'paper_equity_snapshots',
        'paper_events', 'paper_market_events'
    ]
    LOOP
        IF has_table_privilege('authenticated', 'public.' || ro_table, 'INSERT') THEN
            RAISE EXCEPTION 'postflight failed: authenticated can still INSERT into %', ro_table;
        END IF;
        IF NOT has_table_privilege('authenticated', 'public.' || ro_table, 'SELECT') THEN
            RAISE EXCEPTION 'postflight failed: authenticated lost SELECT on %, which this migration does not intend to change', ro_table;
        END IF;
        IF NOT has_table_privilege('service_role', 'public.' || ro_table, 'INSERT') THEN
            RAISE EXCEPTION 'postflight failed: service_role lost INSERT on %', ro_table;
        END IF;
    END LOOP;
END
$$;
