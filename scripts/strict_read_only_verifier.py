import psycopg2
import json
import os
import re

db_url = 'postgresql://postgres.wrkexcjqnidkdrayhlsi:Narendra%40221203221203@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres'

def perform_strict_verification():
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    # 1. Exact Production Database Inventory
    cur.execute("SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;")
    tables = cur.fetchall()
    
    cur.execute("SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default FROM information_schema.columns WHERE table_schema = 'public' ORDER BY table_name, ordinal_position;")
    columns = cur.fetchall()
    
    cur.execute("""
        SELECT 
            tc.table_name, 
            tc.constraint_name, 
            tc.constraint_type,
            kcu.column_name,
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        LEFT JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
        WHERE tc.table_schema = 'public'
        ORDER BY tc.table_name, tc.constraint_name;
    """)
    constraints = cur.fetchall()
    
    cur.execute("SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY tablename, indexname;")
    indexes = cur.fetchall()
    
    cur.execute("SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;")
    rls_status = cur.fetchall()
    
    cur.execute("SELECT tablename, policyname, permissive, roles, cmd, qual, with_check FROM pg_policies WHERE schemaname = 'public' ORDER BY tablename, policyname;")
    policies = cur.fetchall()
    
    cur.execute("""
        SELECT 
            p.proname, 
            pg_get_function_arguments(p.oid) as args, 
            pg_get_function_result(p.oid) as res, 
            p.prosecdef as secdef
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
        ORDER BY p.proname;
    """)
    rpcs = cur.fetchall()
    
    cur.execute("""
        SELECT 
            event_object_table, 
            trigger_name, 
            event_manipulation, 
            action_statement, 
            action_timing 
        FROM information_schema.triggers 
        WHERE event_object_schema IN ('public', 'auth')
        ORDER BY event_object_table, trigger_name;
    """)
    triggers = cur.fetchall()
    
    cur.execute("SELECT table_name, view_definition FROM information_schema.views WHERE table_schema = 'public' ORDER BY table_name;")
    views = cur.fetchall()
    
    cur.execute("SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public' ORDER BY sequence_name;")
    sequences = cur.fetchall()

    # 2. Data Integrity Checks (Orphan Counts)
    cur.execute("SELECT count(*) FROM public.strategies WHERE user_id IS NOT NULL AND user_id::text NOT IN (SELECT id::text FROM public.profiles);")
    orphan_strategies = cur.fetchone()[0]
    
    cur.execute("SELECT count(*) FROM public.exchange_keys WHERE user_id IS NOT NULL AND user_id::text NOT IN (SELECT id::text FROM public.profiles);")
    orphan_exchange_keys = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM public.signals WHERE user_id IS NOT NULL AND user_id::text NOT IN (SELECT id::text FROM public.profiles);")
    orphan_signals = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM public.notifications WHERE user_id IS NOT NULL AND user_id::text NOT IN (SELECT id::text FROM public.profiles);")
    orphan_notifications = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM public.support_tickets WHERE user_id IS NOT NULL AND user_id::text NOT IN (SELECT id::text FROM public.profiles);")
    orphan_support_tickets = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM public.ticket_comments WHERE ticket_id IS NOT NULL AND ticket_id NOT IN (SELECT id FROM public.support_tickets);")
    orphan_ticket_comments = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM public.referral_codes WHERE user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM public.profiles);")
    orphan_referral_codes = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM public.referral_wallets WHERE user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM public.profiles);")
    orphan_referral_wallets = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM public.library_subscriptions WHERE user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM public.profiles);")
    orphan_library_subscriptions = cur.fetchone()[0]

    report = {
        'tables_count': len(tables),
        'columns_count': len(columns),
        'constraints_count': len(constraints),
        'indexes_count': len(indexes),
        'rls_status_count': len(rls_status),
        'policies_count': len(policies),
        'rpcs_count': len(rpcs),
        'triggers_count': len(triggers),
        'views_count': len(views),
        'sequences_count': len(sequences),
        'tables': tables,
        'columns': columns,
        'constraints': constraints,
        'indexes': indexes,
        'rls_status': rls_status,
        'policies': policies,
        'rpcs': rpcs,
        'triggers': triggers,
        'views': views,
        'sequences': sequences,
        'orphan_integrity': {
            'orphan_strategies': orphan_strategies,
            'orphan_exchange_keys': orphan_exchange_keys,
            'orphan_signals': orphan_signals,
            'orphan_notifications': orphan_notifications,
            'orphan_support_tickets': orphan_support_tickets,
            'orphan_ticket_comments': orphan_ticket_comments,
            'orphan_referral_codes': orphan_referral_codes,
            'orphan_referral_wallets': orphan_referral_wallets,
            'orphan_library_subscriptions': orphan_library_subscriptions
        }
    }

    os.makedirs('reports', exist_ok=True)
    with open('reports/strict_read_only_verification.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, default=str)

    print("=== STRICT VERIFICATION DATA COMPILED ===")
    print(f"Tables in Public Schema: {len(tables)}")
    print(f"Columns in Public Schema: {len(columns)}")
    print(f"Constraints: {len(constraints)}")
    print(f"Indexes: {len(indexes)}")
    print(f"RLS Enabled Tables: {sum(1 for r in rls_status if r[1])}/{len(rls_status)}")
    print(f"RLS Policies Active: {len(policies)}")
    print(f"RPC Functions in Public: {len(rpcs)}")
    print(f"Triggers Active: {len(triggers)}")
    print(f"Views: {len(views)}")
    print(f"Sequences: {len(sequences)}")
    print("Orphan Integrity Counts:", report['orphan_integrity'])

    cur.close()
    conn.close()

if __name__ == '__main__':
    perform_strict_verification()
