"""Remediation phase: inspect the current enforcement state of the objects being fixed.

Read-only. Prints the live catalogue facts the remediation depends on, so the fixes are
written against what the database actually has rather than against the migration text.
"""
from __future__ import annotations

import sys

import psycopg2

sys.path.insert(0, "staging")
from dsn import resolve_dsn  # noqa: E402

QUERIES = [
    (
        "BUG056 updated_at column (must exist)",
        """SELECT column_name, data_type
             FROM information_schema.columns
            WHERE table_schema='public' AND table_name='library_subscriptions'
              AND column_name='updated_at'""",
    ),
    (
        "library_strategies triggers",
        """SELECT t.tgname
             FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
            WHERE c.relname='library_strategies' AND NOT t.tgisinternal
            ORDER BY 1""",
    ),
    (
        "library_strategies policies",
        """SELECT p.polname, p.polcmd,
                  pg_get_expr(p.polqual, p.polrelid)      AS using_expr,
                  pg_get_expr(p.polwithcheck, p.polrelid) AS check_expr
             FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
            WHERE c.relname='library_strategies' ORDER BY 1""",
    ),
    (
        "library_strategies table-level grants (browser roles)",
        """SELECT grantee, privilege_type
             FROM information_schema.role_table_grants
            WHERE table_schema='public' AND table_name='library_strategies'
              AND grantee IN ('authenticated','anon') ORDER BY 1,2""",
    ),
    (
        "library_strategies COLUMN-level grants (browser roles)",
        """SELECT grantee, column_name, privilege_type
             FROM information_schema.column_privileges
            WHERE table_schema='public' AND table_name='library_strategies'
              AND grantee IN ('authenticated','anon') ORDER BY 1,2,3""",
    ),
    (
        "moderation-relevant columns on library_strategies",
        """SELECT column_name, data_type, column_default
             FROM information_schema.columns
            WHERE table_schema='public' AND table_name='library_strategies'
              AND column_name IN ('moderation_status','is_active','is_featured',
                                  'price_minor','price','author_id','published_at',
                                  'moderated_by','moderated_at','moderation_notes',
                                  'avg_rating','clone_count','subscriber_count',
                                  'verification_status','evaluation_score')
            ORDER BY 1""",
    ),
    (
        "referral_commissions columns",
        """SELECT column_name, data_type
             FROM information_schema.columns
            WHERE table_schema='public' AND table_name='referral_commissions'
            ORDER BY ordinal_position""",
    ),
    (
        "referral_commissions constraints / indexes",
        """SELECT conname, pg_get_constraintdef(oid)
             FROM pg_constraint
            WHERE conrelid = 'public.referral_commissions'::regclass ORDER BY 1""",
    ),
    (
        "referral_commissions indexes",
        """SELECT indexname, indexdef FROM pg_indexes
            WHERE schemaname='public' AND tablename='referral_commissions' ORDER BY 1""",
    ),
    (
        "installed process_referral_commission source (first 1200 chars)",
        """SELECT left(pg_get_functiondef(oid), 1200)
             FROM pg_proc WHERE proname='process_referral_commission'""",
    ),
    (
        "SECURITY DEFINER functions + EXECUTE acl",
        """SELECT p.proname, p.prosecdef, p.proacl::text
             FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname='public' AND p.prosecdef
            ORDER BY 1""",
    ),
    (
        "strategy_deployments live-uniqueness index (021)",
        """SELECT indexname, indexdef FROM pg_indexes
            WHERE schemaname='public' AND tablename='strategy_deployments'
              AND indexname='uq_strategy_deployments_one_live'""",
    ),
    (
        "strategies table columns (for the PUT allowlist)",
        """SELECT column_name, data_type
             FROM information_schema.columns
            WHERE table_schema='public' AND table_name='strategies'
            ORDER BY ordinal_position""",
    ),
]


def main() -> int:
    conn = psycopg2.connect(resolve_dsn())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for label, sql in QUERIES:
                print("=" * 78)
                print(label)
                print("-" * 78)
                try:
                    cur.execute(sql)
                    rows = cur.fetchall()
                    if not rows:
                        print("  (no rows)")
                    for row in rows:
                        print("  ", row)
                except psycopg2.Error as exc:
                    print(f"   QUERY FAILED: {exc.pgcode} {str(exc).splitlines()[0]}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
