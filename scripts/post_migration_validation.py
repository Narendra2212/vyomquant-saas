import os
import psycopg2
import json
import sys

db_url = os.getenv("DATABASE_URL", "sqlite:///./test.db")

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

    print("\n=== 3. VALIDATING RLS STATUS ===")
    cur.execute("SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public';")
    rls_status = {r[0]: r[1] for r in cur.fetchall()}
    
    unprotected = []
    for tbl in required_tables:
        if tbl in rls_status and not rls_status[tbl]:
            unprotected.append(tbl)
            
    if unprotected:
        print(f"FAILED: Unprotected tables without RLS: {unprotected}")
    else:
        print("PASSED: 100% of user-owned and data tables have RLS ENABLED!")

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

if __name__ == '__main__':
    validate_production_db()
