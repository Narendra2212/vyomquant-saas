-- ============================================================================
--  056 - VAULT-01: a credential state the UI can act on, and the column the
--                  dashboard has been asking for all along
-- ============================================================================
--
--  WHAT VAULT-01 ASKS FOR
--  ----------------------
--  "Their existing rows should be deleted or flagged rather than left to raise,
--   because the current failure mode is an exception from _decrypt at use time
--   rather than a clear 'reconnect your exchange' state the UI can act on."
--
--  This migration provides the state. It does NOT decide which rows are in it --
--  that is a measurement, not a schema fact, and it is made by
--  scripts/quarantine_undecryptable_exchange_keys.py, which marks a row only
--  after actually failing to decrypt it. Asserting the affected user ids in DDL
--  would hardcode the findings of one afternoon into the schema forever.
--
--  THE DEFECT FOUND WHILE IMPLEMENTING IT, WHICH IS LARGER
--  ------------------------------------------------------
--  dashboard_aggregation_service._fetch_exchange_credentials tries two
--  projections, widest first, and the narrow one is the documented fallback for
--  "a schema that predates the validity columns":
--
--      _EXCHANGE_KEY_VALIDITY_COLUMNS = "is_active, validation_status,
--                                        last_validated_at, can_trade"
--      _EXCHANGE_KEY_BASE_COLUMNS     = "exchange_id, updated_at"
--
--  exchange_keys has never had updated_at. It was created by
--  001_strategy_architecture.sql with user_id, exchange_id, encrypted_api_key,
--  encrypted_secret_key, encrypted_password, created_at -- and nothing since
--  added it. So the FALLBACK names a missing column too, and BOTH projections are
--  rejected. Measured against production PostgREST on 2026-10-24:
--
--      select=exchange_id,updated_at,is_active,validation_status,...
--        -> HTTP 400 {"code":"42703","message":"column exchange_keys.updated_at
--                     does not exist"}
--      select=exchange_id,updated_at
--        -> HTTP 400 {"code":"42703", ... same ...}
--      select=exchange_id,created_at
--        -> HTTP 200, 2 rows
--
--  _fetch_exchange_credentials therefore returns [] for EVERY user, and
--  get_exchange_data reports total_exchanges=0, connected_exchanges=0,
--  exchanges=[], can_trade=false to every account that has connected a venue.
--  Not a degraded reading -- the venue list is empty for everybody.
--
--  That also makes VAULT-01's requirement unreachable on its own terms: a state
--  "the UI can act on" cannot be acted on while the read that would surface it
--  returns nothing. Adding updated_at is part of this fix, not a drive-by.
--
--  WHY THESE COLUMN TYPES
--  ----------------------
--  can_trade is NULLABLE WITH NO DEFAULT, deliberately. credential_validity()
--  distinguishes True / False / None, and documents None as "the record does not
--  answer", which Requirement 2.6 makes non-tradable WITHOUT recording a negative
--  fact about the credential. A DEFAULT false would convert "nobody has checked"
--  into "we checked and it cannot trade", which is the fabrication that whole
--  module was written to remove. Same reasoning for validation_status and
--  last_validated_at: NULL means unvalidated, and unvalidated is the truth.
--
--  is_active DEFAULTS TRUE because it means "the user enabled this key", and
--  every existing row was enabled by the user who created it. It does NOT mean
--  "the credential works" -- credential_validity's comment is explicit that
--  is_active alone is not affirmative evidence.
--
--  NO CHECK CONSTRAINT ON validation_status, and that is a decision rather than
--  an omission. The dashboard matches it against two permissive frozensets and
--  deliberately leaves 'unvalidated' and 'pending' in NEITHER, so the unknown has
--  spellings that are valid but assert nothing. A CHECK would be stricter than
--  the contract the reader implements, and would make any future producer's
--  spelling a write failure in production. The one spelling this change
--  introduces -- 'unreadable' -- is pinned in Python as
--  credential_key_integrity.UNREADABLE_STATUS and used by both the writer and the
--  reader, so it cannot drift even without a constraint.
--
--  IDEMPOTENT, and safe to run against a database that already has it.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
--  updated_at: the column both projections already name.
--  Backfilled from created_at rather than now(), so an untouched row does not
--  claim it was modified at migration time. "last changed" is what the
--  dashboard renders as last_sync; now() would make every stale row look fresh.
-- ---------------------------------------------------------------------------
ALTER TABLE public.exchange_keys
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

UPDATE public.exchange_keys
   SET updated_at = created_at
 WHERE updated_at IS NULL;

ALTER TABLE public.exchange_keys
    ALTER COLUMN updated_at SET DEFAULT timezone('utc'::text, now());

ALTER TABLE public.exchange_keys
    ALTER COLUMN updated_at SET NOT NULL;

-- ---------------------------------------------------------------------------
--  The four validity columns dashboard_aggregation_service has been asking for.
--  Its own comment: "the moment a migration records validity the dashboard reads
--  it without a change here." This is that migration.
-- ---------------------------------------------------------------------------
ALTER TABLE public.exchange_keys
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE public.exchange_keys
    ADD COLUMN IF NOT EXISTS validation_status TEXT;

ALTER TABLE public.exchange_keys
    ADD COLUMN IF NOT EXISTS last_validated_at TIMESTAMPTZ;

ALTER TABLE public.exchange_keys
    ADD COLUMN IF NOT EXISTS can_trade BOOLEAN;

COMMENT ON COLUMN public.exchange_keys.updated_at IS
    'When this row last changed. Rendered as last_sync by get_exchange_data. '
    'Backfilled from created_at by migration 056; maintained by trigger.';

COMMENT ON COLUMN public.exchange_keys.is_active IS
    'The user enabled this key. NOT evidence that the credential works -- see '
    'credential_validity(), which refuses to treat is_active alone as affirmative.';

COMMENT ON COLUMN public.exchange_keys.validation_status IS
    'What a check concluded about this credential, or NULL for never checked. '
    'NULL/unvalidated/pending are the UNKNOWN and assert nothing. The spelling '
    '''unreadable'' means the stored ciphertext does not decrypt under any '
    'configured master key, so the credential cannot be recovered and the user '
    'must re-enter it (VAULT-01). That is distinct from ''revoked''/''invalid'', '
    'which say the VENUE rejected a key we could read.';

COMMENT ON COLUMN public.exchange_keys.last_validated_at IS
    'When validation_status was last written. NULL while never validated.';

COMMENT ON COLUMN public.exchange_keys.can_trade IS
    'Tri-state, and NULL is meaningful: nothing has established that this '
    'credential can place an order. Never defaulted to false -- that would record '
    'a negative finding nobody measured.';

-- ---------------------------------------------------------------------------
--  Keep updated_at honest on every future write, using the function 001 already
--  defines and attaches to every table it creates. Created here only if some
--  earlier migration has not already defined it.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = timezone('utc'::text, now());
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_exchange_keys_updated_at ON public.exchange_keys;
CREATE TRIGGER update_exchange_keys_updated_at
    BEFORE UPDATE ON public.exchange_keys
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

-- ---------------------------------------------------------------------------
--  A partial index on the quarantined rows. The boot check reads "every row NOT
--  marked unreadable", and the operator view reads "which rows are". Both are
--  selective, and the quarantined set is expected to stay tiny.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_exchange_keys_unreadable
    ON public.exchange_keys (user_id, exchange_id)
 WHERE validation_status = 'unreadable';

COMMIT;
