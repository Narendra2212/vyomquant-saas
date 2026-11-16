-- ============================================================================
-- 068_strat01_strategy_name_uniqueness.sql
--
-- STRAT-01. A user could create any number of strategies with the same name.
-- Nothing in the database stopped it and nothing in the application checked.
--
-- WHAT WAS MEASURED, AGAINST PRODUCTION, BEFORE THIS FILE WAS WRITTEN.
-- Counted on the live server (PostgreSQL 17.6, pooler, read-only session,
-- 2026-11-16):
--
--   public.strategies                      207 rows
--   public.strategies WHERE archived_at IS NULL   168 rows
--   distinct owners                         78
--   colliding (user_id, lower(name)) groups  2
--   rows over the limit                      6
--
-- So a unique index COULD NOT have been created when this was measured. The
-- two groups were:
--
--   owner 52384fe1…  6 rows, all created 2026-09-27 between 17:11:28.993 and
--                    17:13:52.187 — five of them inside 900 milliseconds —
--                    all status=stopped, all current_version=v1.0, all
--                    dag_hash NULL, and all SIX with zero dependent rows in
--                    every one of the six tables that reference strategies.
--   owner ac9f0659…  2 rows, created 11.5 seconds apart on 2026-11-13. The
--                    first (d2d2fbc7, stopped, v1.0) has zero dependents.
--                    The second (c10a5bc3, running, v2.0) has 2 versions,
--                    2 deployments and 74 signals and is LIVE.
--
-- THAT PATTERN IS NOT USERS NAMING THINGS BADLY. Five identical inserts in
-- under a second, same content, same version, is a create path with no
-- idempotency key being driven by a double submit or a client retry. This
-- index constrains the SYMPTOM. The create path is the defect, and it is
-- tracked separately — adding a constraint without fixing the writer converts
-- a silent data problem into a user-facing 23505.
--
-- WHY THE RECONCILIATION IS NOT IN THIS FILE.
-- 021_strategy_deployment_live_uniqueness.sql states the rule this repository
-- already follows: "a unique index that cannot be built is not a migration,
-- and reconciling live duplicate deployments is an operational decision, not
-- a schema one." Archiving six rows of customer data is an operational act
-- that needs a named decision, a dry run and an audit trail. It lives in
-- `scripts/reconcile_duplicate_strategy_names.py`, which must be run (and its
-- report reviewed) BEFORE this file. SECTION 1 below refuses if it was not.
--
-- WHY A PREFLIGHT THAT REFUSES, RATHER THAN A BEST EFFORT.
-- A failed CREATE UNIQUE INDEX leaves an INVALID index behind that somebody
-- then has to notice and drop. Refusing with a legible message costs one
-- query and leaves the catalogue untouched.
--
-- WHY lower(name) AND NOT name.
-- Users do not distinguish "Momentum v2" from "momentum v2"; presenting them
-- as two strategies is the same defect in a different costume. lower() is the
-- strictly stronger predicate, so it also subsumes exact-case collisions.
-- The expression must be repeated verbatim in any query that wants to use
-- this index.
--
-- WHY archived_at IS NULL.
-- Archiving is this schema's soft delete (`idx_strategies_archived_at` is
-- already partial on the same predicate, from 005a_strategy_archive.sql). A
-- user must be able to archive "Momentum" and later create a new strategy by
-- that name, and must be able to archive several same-named strategies
-- without the archive itself becoming a uniqueness conflict. Constraining
-- archived rows would break both.
--
-- WHY NOT CONCURRENTLY, DELIBERATELY.
-- CREATE INDEX CONCURRENTLY cannot run inside a transaction block, and this
-- repository's runners (`scripts/provision_database.py`, one file per
-- transaction) would be unable to apply the file at all — the same reasoning
-- 021 records. The cost of the plain form was measured rather than assumed:
-- 168 live rows in a 56 kB heap, so the ACCESS EXCLUSIVE lock is held for
-- single-digit milliseconds. Should this table grow by orders of magnitude,
-- apply the index out-of-band with the CONCURRENTLY form given in the
-- rollback file's header and then re-run this migration, which will see the
-- index already present and no-op.
--
-- PROVISIONING ORDER. Needs public.strategies, declared by
-- 020_declare_pre_existing_tables.sql. 020 runs first regardless of its
-- number — see backend_app/migrations/PROVISIONING_ORDER.md.
--
-- IDEMPOTENT. IF NOT EXISTS on the index; a second run is a notice.
--
-- ROLLBACK. backend_app/migrations/rollback/068_strat01_rollback.sql
-- ============================================================================

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 1 — Preflight. Refuse legibly rather than leaving an INVALID index.
-- ─────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    collision_groups integer;
    excess_rows      integer;
    sample           text;
BEGIN
    IF to_regclass('public.strategies') IS NULL THEN
        RAISE EXCEPTION
            '068 requires public.strategies, which is declared by '
            '020_declare_pre_existing_tables.sql. Apply 020 first. See '
            'backend_app/migrations/PROVISIONING_ORDER.md.';
    END IF;

    SELECT count(*), coalesce(sum(n) - count(*), 0)
      INTO collision_groups, excess_rows
      FROM (
          SELECT count(*) AS n
            FROM public.strategies
           WHERE archived_at IS NULL
           GROUP BY user_id, lower(name)
          HAVING count(*) > 1
      ) AS g;

    IF collision_groups > 0 THEN
        SELECT string_agg(
                   format('owner %s has %s rows sharing one name',
                          left(user_id::text, 8), cnt),
                   '; ' ORDER BY cnt DESC)
          INTO sample
          FROM (
              SELECT user_id, count(*) AS cnt
                FROM public.strategies
               WHERE archived_at IS NULL
               GROUP BY user_id, lower(name)
              HAVING count(*) > 1
               LIMIT 5
          ) AS s;

        RAISE EXCEPTION
            '068 REFUSED: % colliding (user_id, lower(name)) group(s) with % '
            'row(s) over the limit already exist among live strategies, so '
            'CREATE UNIQUE INDEX would fail and leave an INVALID index. %. '
            'Run `python scripts/reconcile_duplicate_strategy_names.py '
            '--dsn <dsn>` to see the report, review which row each group '
            'keeps, then re-run it with --apply. Then apply this migration.',
            collision_groups, excess_rows, sample;
    END IF;

    RAISE NOTICE '068 preflight: no live (user_id, lower(name)) collisions. '
                 'Safe to build the unique index.';
END $$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 2 — At most one live strategy per (owner, case-folded name).
--
-- The losing INSERT fails with 23505 unique_violation, which the API layer
-- must translate into a named refusal rather than a 500.
-- ─────────────────────────────────────────────────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS uq_strategies_owner_name_live
    ON public.strategies (user_id, lower(name))
    WHERE archived_at IS NULL;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 3 — Confirm what landed, so a hand-applied run leaves a trace.
-- ─────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public'
           AND tablename  = 'strategies'
           AND indexname  = 'uq_strategies_owner_name_live'
    ) THEN
        RAISE NOTICE '068: uq_strategies_owner_name_live present — one live '
                     'strategy name per owner is now enforced by the '
                     'database. A duplicate create fails with 23505.';
    ELSE
        RAISE EXCEPTION '068: uq_strategies_owner_name_live was not created.';
    END IF;
END $$;
