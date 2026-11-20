import os
import psycopg2
import json
import sys

db_url = os.getenv("DATABASE_URL", "sqlite:///./test.db")


def fetch_privileged_tables_without_rls(cur):
    """``[(tablename, rowsecurity), ...]`` for every ``public`` table that grants
    ``anon`` or ``authenticated`` any privilege.

    DB-03: the RLS check this replaced walked a hardcoded ``required_tables`` list, so
    a customer table added by a later migration -- Supabase's default privilege shape
    grants ``anon``/``authenticated`` something on most new tables automatically -- was
    invisible to it unless someone remembered to add its name here too. This is the
    other direction: it asks the catalog which tables those two roles can actually
    touch, so a forgotten ``ENABLE ROW LEVEL SECURITY`` on a brand-new table is caught
    without this file ever being edited again.
    """
    cur.execute("""
        SELECT t.tablename, t.rowsecurity
        FROM pg_tables t
        WHERE t.schemaname = 'public'
          AND EXISTS (
              SELECT 1 FROM information_schema.table_privileges g
              WHERE g.table_schema = 'public'
                AND g.table_name = t.tablename
                AND g.grantee IN ('anon', 'authenticated')
          )
        ORDER BY t.tablename;
    """)
    return cur.fetchall()


#: Tables deliberately exempt from the privilege-hardening check: global, non-tenant
#: catalog data that is meant to be world-readable (a pricing page needs this), not a
#: per-customer leak this check exists to catch. DB-04: found via this session's first
#: real run of this broader, previously-uninvoked check -- plans/plan_features/
#: plan_limits/plan_prices/plan_migration_mapping (all from the single migration
#: migrations/create_plan_tables.sql) grant anon/authenticated SELECT with RLS
#: disabled, and carry no ownership column (user_id/tenant_id/profile_id/owner_id/
#: author_id) anywhere in their schema -- migrations/hardening/999_privilege_hardening
#: .sql's own narrower backstop already correctly does not flag them, for exactly that
#: reason (it only flags a table that both grants a browser role a privilege AND
#: carries an ownership column). Adding a table here is a product decision ("this data
#: has no customer identity and is safe to leave world-readable"), not a code fix --
#: see DB-04's own ledger entry for the full reasoning.
PUBLIC_CATALOG_TABLES = frozenset({
    "plans", "plan_features", "plan_limits", "plan_prices", "plan_migration_mapping",
})


def partition_privileged_tables(rows, allowlist=PUBLIC_CATALOG_TABLES):
    """Split :func:`fetch_privileged_tables_without_rls`'s rows into ``(checked,
    allowlisted)``. ``checked`` is what :func:`evaluate_privilege_hardening` should
    decide on; ``allowlisted`` is reported, never silently dropped, so adding a table
    to the allowlist is still visible in the log rather than making it disappear.

    Kept separate from :func:`evaluate_privilege_hardening` on purpose: that function's
    own docstring states no table name is special-cased inside it, and this keeps that
    true -- the allowlist is a filter applied to its input, not a branch inside its
    logic.
    """
    checked = [(name, rls) for name, rls in rows if name not in allowlist]
    allowlisted = [(name, rls) for name, rls in rows if name in allowlist]
    return checked, allowlisted


def evaluate_privilege_hardening(rows):
    """Pure pass/fail decision from :func:`fetch_privileged_tables_without_rls`'s rows.

    Kept separate from the database call so the decision logic has a test that needs
    no live Postgres -- the same split ``scripts/verify_rls.py`` already uses for its
    own catalog checks.
    """
    unprotected = sorted(name for name, rls_enabled in rows if not rls_enabled)
    if unprotected:
        print(
            f"FAILED: {len(unprotected)} table(s) grant anon/authenticated a privilege "
            f"but do NOT have RLS enabled: {unprotected}"
        )
        return False
    print(
        f"PASSED: every one of the {len(rows)} table(s) granting anon/authenticated a "
        f"privilege has RLS enabled."
    )
    return True


def validate_production_db():
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    print("=== 1. VALIDATING TABLE EXISTENCE ===")
    required_tables = [
        'profiles', 'exchange_keys', 'strategies', 'library_strategies', 
        'library_subscriptions', 'deployment_permissions', 'library_ratings',
        'risk_settings', 'strategy_limits', 'risk_settings_audit',
        'strategy_backtests', 'strategy_research_reports', 'strategy_deployments',
        'strategy_versions', 'notifications', 'notification_settings',
        'security_logs', 'support_tickets', 'ticket_comments',
        'referral_codes', 'referral_relationships', 'referral_commissions',
        'referral_wallets', 'referral_payouts', 'referral_profiles',
        'exchange_connections', 'billing_invoices', 'invoices',
        'orders', 'fills', 'positions', 'signals', 'dag_tasks',
        'execution_records', 'reconciliation_mismatches', 'idempotency_keys'
    ]
    
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';")
    live_tables = set(r[0] for r in cur.fetchall())
    
    missing_tables = [t for t in required_tables if t not in live_tables]
    print(f"Total live public tables: {len(live_tables)}")
    if missing_tables:
        print(f"FAILED: Missing tables: {missing_tables}")
    else:
        print(f"PASSED: 100% of {len(required_tables)} required tables exist in production!")

    print("\n=== 2. VALIDATING COLUMNS ===")
    required_columns = {
        'profiles': ['id', 'email', 'available_discounts'],
        'strategies': ['id', 'user_id', 'is_active', 'dag_config', 'status'],
        'library_strategies': ['id', 'subscriber_count', 'is_active', 'avg_rating'],
        'risk_settings': ['id', 'user_id', 'max_daily_loss', 'max_drawdown_pct'],
        'strategy_limits': ['id', 'user_id', 'strategy_id', 'max_drawdown_pct'],
        'notifications': ['id', 'user_id', 'title', 'read', 'created_at'],
        'support_tickets': ['id', 'user_id', 'subject', 'status'],
        'referral_codes': ['id', 'user_id', 'code'],
        'library_subscriptions': ['id', 'library_id', 'user_id', 'status']
    }
    
    col_failures = []
    for tbl, cols in required_columns.items():
        cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = '{tbl}';")
        live_cols = set(r[0] for r in cur.fetchall())
        for c in cols:
            if c not in live_cols:
                col_failures.append(f"{tbl}.{c}")
                
    if col_failures:
        print(f"FAILED: Missing columns: {col_failures}")
    else:
        print("PASSED: All critical reconciled columns exist!")

    print("\n=== 3. VALIDATING RLS STATUS (every anon/authenticated-privileged table, not just required_tables) ===")
    # DB-03: this used to walk `required_tables` -- the same fixed list section 1
    # checks for existence -- so a customer table that migration 6 months from now
    # grants anon/authenticated a privilege on, but forgets to ENABLE ROW LEVEL
    # SECURITY, would never be examined unless someone also remembered to add its name
    # here. fetch_privileged_tables_without_rls asks the catalog which tables those
    # roles can actually reach, so nothing added later can go unchecked by omission.
    checked_rows, allowlisted_rows = partition_privileged_tables(
        fetch_privileged_tables_without_rls(cur)
    )
    if allowlisted_rows:
        print(
            f"ALLOWLISTED (public catalog data, not customer data, see "
            f"PUBLIC_CATALOG_TABLES): {sorted(name for name, _ in allowlisted_rows)}"
        )
    privilege_hardening_passed = evaluate_privilege_hardening(checked_rows)

    print("\n=== 4. VALIDATING STORED RPC FUNCTIONS ===")
    required_rpcs = [
        'increment_ml_addon',
        'create_referral_code_for_user',
        'process_referral_commission',
        'reverse_referral_commission'
    ]
    cur.execute("""
        SELECT p.proname 
        FROM pg_proc p 
        JOIN pg_namespace n ON n.oid = p.pronamespace 
        WHERE n.nspname = 'public';
    """)
    live_rpcs = set(r[0] for r in cur.fetchall())
    missing_rpcs = [r for r in required_rpcs if r not in live_rpcs]
    if missing_rpcs:
        print(f"FAILED: Missing RPC functions: {missing_rpcs}")
    else:
        print(f"PASSED: 100% of required RPC functions ({required_rpcs}) exist!")

    print("\n=== 5. TENANT ISOLATION SIMULATION TEST ===")
    # Test RPC functions execution safely
    cur.execute("SELECT proname, prosecdef FROM pg_proc WHERE proname IN ('increment_ml_addon', 'create_referral_code_for_user');")
    sec_defs = cur.fetchall()
    print("RPC Security Definer verification:", sec_defs)
    
    print("\n=== 6. RE-EXPORTING FULL DB METADATA ===")
    cur.execute("SELECT table_schema, table_name, table_type FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;")
    tables = cur.fetchall()
    cur.execute("SELECT table_schema, table_name, column_name, data_type, udt_name, is_nullable, column_default FROM information_schema.columns WHERE table_schema = 'public' ORDER BY table_name, ordinal_position;")
    columns = cur.fetchall()
    cur.execute("SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual, with_check FROM pg_policies WHERE schemaname = 'public';")
    policies = cur.fetchall()
    
    meta = {
        'tables_count': len(tables),
        'columns_count': len(columns),
        'policies_count': len(policies),
        'tables': tables,
        'columns': columns,
        'policies': policies
    }
    with open('reports/post_reconciliation_metadata.json', 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, default=str)
    print(f"Updated metadata exported: {len(tables)} tables, {len(columns)} columns, {len(policies)} RLS policies.")

    cur.close()
    conn.close()

    return privilege_hardening_passed

if __name__ == '__main__':
    if not validate_production_db():
        sys.exit(1)
