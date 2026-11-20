-- 072_sup02_support_ticket_declared_columns.sql
--
-- SUP-02. `public.support_tickets` is the support channel's only durable
-- record. Its one writer names four columns this schema has never declared,
-- and `public.ticket_comments` is sent a fifth:
--
--     supabase.table("support_tickets").insert({        support.py:459
--         "related_feature": body.related_feature,   <-- undeclared
--         "strategy_id":     body.strategy_id,       <-- undeclared
--         "order_id":        body.order_id,          <-- undeclared
--         "attachment":      {...} or None,          <-- undeclared
--         ... id, user_id, subject, description, category, priority,
--             status, created_at, updated_at, has_unread, comment_count
--     }).execute()
--
--     supabase.table("ticket_comments").insert({       support.py:670
--         "attachment": {...} or None,               <-- undeclared
--         ... id, ticket_id, user_id, message, is_staff, created_at
--     }).execute()
--
-- PostgREST rejects the whole statement on an undefined column (PGRST204), so
-- neither write can succeed. Measured read-only against production:
--
--     support_tickets columns              : 12   (none of the four)
--     ticket_comments columns              :  7   (no attachment)
--     newest ticket in production          : 2026-08-18
--     commit that added the four keys      : f0e4fc6a, 2026-08-29
--
-- The dates are the finding. Ticket creation worked, the keys were added
-- eleven days after the last ticket landed, and nothing has been filed since.
-- Both migration sources that declare these tables -- add_support_tables.sql
-- and 006_reconcile_production_database.sql (PART ~11) -- agree on the same
-- column sets, so this is not drift between files; the columns were simply
-- never added.
--
-- WHAT WAS BROKEN BY IT, and why it is worse than an empty table:
--
--   1. THE USER IS TOLD THE TICKET WAS FILED. The insert sits inside
--      `try/except ... logger.warning(...)` and the except body writes the
--      ticket into `_in_memory_tickets`, a module-level dict. The endpoint
--      then returns 200 with a real ticket id. So a user reporting a failed
--      order receives a reference number for a ticket that was never stored.
--   2. THE FALLBACK CANNOT HOLD IT EITHER. The API runs `WORKERS=2`, so the
--      dict is visible to one uvicorn worker out of two -- a follow-up GET
--      that lands on the other answers 404 -- and it is lost on restart. The
--      API's task was being replaced every ~9.5 minutes during the 2026-11-17
--      incident, so that window is short.
--   3. SUP-01 ALREADY FIXED A DIFFERENT CAUSE OF THE SAME SYMPTOM. Commit
--      9311a373 replaced a `"tkt_"`-prefixed id with a real `uuid4()` because
--      the prefix raised `invalid input syntax for type uuid`, and its comment
--      states "a real UUID lets the real insert succeed, which is the actual
--      fix". It did not, because this second cause was already present and is
--      fatal on its own. Two independent causes, one symptom.
--   4. THE TESTS COULD NOT SEE IT. `tests/test_sup01_ticket_ids_are_real_uuids`
--      asserts the row reaches "the fake's real table rather than the in-memory
--      fallback", and `test_support_feature_e2e` posts `strategy_id`,
--      `order_id`, `related_feature` and `attachment` and asserts 200 -- all
--      against a stub that accepts any key. A fake with no schema cannot
--      reject an undeclared column, which is why this needs a PostgreSQL-backed
--      test and gets one in tests/test_sup02_support_columns_postgres.py.
--
-- WHY DECLARE THEM RATHER THAN DELETE THE REFERENCES.
-- Same resolution as BILL-07 (070) and for the same reason: the values
-- genuinely arrive. `SupportCenter.jsx` renders an input for each one --
-- strategy_id (line 656), order_id (669), related_feature (182 in the payload)
-- -- and the request model declares all four with explicit max_lengths. The
-- opposite resolution was correct for `profiles.volume_usd`, where nothing
-- produced the value. Here something does, and dropping the keys would discard
-- the diagnostic context that makes an algo-trading support ticket actionable.
--
-- TYPES MATCH THE REQUEST MODEL, not a guess. support.py declares
-- `related_feature`/`strategy_id`/`order_id` as `Optional[str]` with
-- `max_length=100`, so varchar(100) each. `strategy_id` and `order_id` are
-- deliberately NOT foreign keys: the model accepts free text up to 100 chars,
-- the UI collects it by hand, and a user mistyping an order id must still be
-- able to file the ticket -- an FK would reject the report instead of the
-- typo. `attachment` is `AttachmentMeta` serialised with `.dict()`, so jsonb.
--
-- WHY NULLABLE AND NO DEFAULTS. All five are optional on the request. A
-- default would assert a value the user did not give, and every one of the
-- six existing production tickets predates these columns -- backfilling them
-- would invent diagnostic context for tickets that carried none.
--
-- WHAT THIS FILE DOES NOT DO. It does not make file attachments real. There is
-- no storage bucket for them (production has exactly one bucket,
-- `strategy-images`) and `attachment` holds metadata only -- the UI's control
-- is a text input labelled "Attachment Filename / Log Trace", marked in source
-- as a "Safe Attachment Simulation". The FAQ answer claiming users "can attach
-- log files (.log, .txt, .json, .csv) or screenshots" is corrected in the same
-- commit to describe what actually happens, rather than being left to promise
-- an upload that no endpoint accepts. Real uploads remain open work.
--
-- IDEMPOTENT. ADD COLUMN IF NOT EXISTS throughout.
--
-- ROLLBACK. backend_app/migrations/rollback/072_sup02_rollback.sql
-- ============================================================================

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 0 — PREFLIGHT
-- ─────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('public.support_tickets') IS NULL THEN
        RAISE EXCEPTION
            '072 precondition failed: public.support_tickets does not exist. '
            'It is declared by migrations/006_reconcile_production_database.sql '
            'and migrations/add_support_tables.sql. Apply the numbered SQL set '
            'first; see backend_app/migrations/PROVISIONING_ORDER.md.';
    END IF;

    IF to_regclass('public.ticket_comments') IS NULL THEN
        RAISE EXCEPTION
            '072 precondition failed: public.ticket_comments does not exist, '
            'but public.support_tickets does. The two are declared together; a '
            'database with one and not the other is provisioned from a partial '
            'run, and SECTION 2 below would add a column to nothing.';
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 1 — THE FOUR COLUMNS THE TICKET WRITER HAS ALWAYS NAMED
-- ─────────────────────────────────────────────────────────────────────────
ALTER TABLE public.support_tickets
    ADD COLUMN IF NOT EXISTS related_feature varchar(100),
    ADD COLUMN IF NOT EXISTS strategy_id     varchar(100),
    ADD COLUMN IF NOT EXISTS order_id        varchar(100),
    ADD COLUMN IF NOT EXISTS attachment      jsonb;

COMMENT ON COLUMN public.support_tickets.related_feature IS
    'Which product area the ticket is about, as the reporter named it. Free '
    'text, max 100 chars, matching SupportTicketCreate.related_feature. NULL '
    'on tickets filed before the field existed -- honest, not back-dated.';
COMMENT ON COLUMN public.support_tickets.strategy_id IS
    'Strategy the reporter is complaining about. Deliberately NOT a foreign '
    'key to strategies: the field is hand-typed free text and a mistyped id '
    'must not stop someone filing a support ticket.';
COMMENT ON COLUMN public.support_tickets.order_id IS
    'Order the reporter is complaining about. Free text for the same reason as '
    'strategy_id -- an unrecognised order id is itself worth reporting.';
COMMENT ON COLUMN public.support_tickets.attachment IS
    'AttachmentMeta as sent: filename, content_type and size. METADATA ONLY -- '
    'no file is stored anywhere and no storage bucket exists for one. The UI '
    'control is a text input for a filename or log trace, not a file picker.';

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 2 — THE COLUMN THE COMMENT WRITER NAMES
--
-- Both comment writers send it: the user reply at support.py:670 and the staff
-- reply at :916. The staff payload omits it today, but the column is declared
-- once for the table rather than per caller.
-- ─────────────────────────────────────────────────────────────────────────
ALTER TABLE public.ticket_comments
    ADD COLUMN IF NOT EXISTS attachment jsonb;

COMMENT ON COLUMN public.ticket_comments.attachment IS
    'AttachmentMeta on a reply. Metadata only, as on support_tickets.attachment.';

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 3 — VERIFY, IN THE SAME TRANSACTION
--
-- The whole point of this file is that a missing column is silent at the
-- application layer, so it must not be silent here. Asserting the end state
-- rather than trusting five ADD COLUMNs.
-- ─────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(t || '.' || c, ', ' ORDER BY t, c) INTO missing
      FROM (VALUES
              ('support_tickets', 'related_feature'),
              ('support_tickets', 'strategy_id'),
              ('support_tickets', 'order_id'),
              ('support_tickets', 'attachment'),
              ('ticket_comments', 'attachment')
           ) AS want(t, c)
     WHERE NOT EXISTS (
             SELECT 1 FROM information_schema.columns ic
              WHERE ic.table_schema = 'public'
                AND ic.table_name = want.t
                AND ic.column_name = want.c);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            '072 failed: still undeclared after this migration: %. The support '
            'ticket insert will keep raising PGRST204 and keep reporting '
            'success to the user.', missing;
    END IF;

    RAISE NOTICE
        '072 SUP-02: support_tickets gained related_feature, strategy_id, '
        'order_id, attachment; ticket_comments gained attachment. The ticket '
        'insert can now succeed. Attachments remain metadata-only.';
END $$;
