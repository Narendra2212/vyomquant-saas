import os
import re
import json
import psycopg2
from urllib.parse import urlparse

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")

def run_strict_audit():
    print("================================================================================")
    print("STRICT INDEPENDENT REPOSITORY-WIDE AUDIT ENGINE")
    print("================================================================================")
    
    # 1. DATABASE AUDIT DIRECT FROM PRODUCTION POSTGRESQL
    print("\n[1/6] Connecting to live Supabase PostgreSQL instance...")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    # Tables & Columns
    cur.execute("""
        SELECT table_name, column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position;
    """)
    db_cols = cur.fetchall()
    tables = {}
    for tname, cname, dtype, is_null, cdef in db_cols:
        if tname not in tables:
            tables[tname] = []
        tables[tname].append({
            "column": cname,
            "type": dtype,
            "nullable": is_null == "YES",
            "default": cdef
        })
        
    print(f"Verified {len(tables)} public database tables.")
    
    # Primary Keys
    cur.execute("""
        SELECT tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = 'public';
    """)
    pks = dict(cur.fetchall())
    
    # Foreign Keys
    cur.execute("""
        SELECT
            tc.table_name, kcu.column_name,
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public';
    """)
    fks = cur.fetchall()
    
    # Unique Constraints
    cur.execute("""
        SELECT tc.table_name, kcu.column_name, tc.constraint_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'UNIQUE' AND tc.table_schema = 'public';
    """)
    uniques = cur.fetchall()
    
    # RLS Status and Policies
    cur.execute("""
        SELECT c.relname as table_name, c.relrowsecurity as rls_enabled
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r';
    """)
    rls_tables = dict(cur.fetchall())
    
    cur.execute("""
        SELECT tablename, policyname, permissive, roles, cmd, qual, with_check
        FROM pg_policies
        WHERE schemaname = 'public';
    """)
    policies = cur.fetchall()
    
    # RPC Functions
    cur.execute("""
        SELECT routine_name, routine_type, data_type
        FROM information_schema.routines
        WHERE routine_schema = 'public';
    """)
    rpcs = cur.fetchall()
    
    cur.close()
    conn.close()
    
    # 2. BACKEND REST & WEBSOCKET AUDIT
    print("\n[2/6] Inspecting all Backend Routers & WebSocket Handlers...")
    # Scan main.py
    with open("backend_app/main.py", "r", encoding="utf-8") as f:
        main_src = f.read()
        
    mount_matches = re.findall(r'app\.include_router\(\s*([\w\.]+)(?:,\s*prefix=["\']([^"\']+)["\'])?', main_src)
    router_mounts = {}
    for r_var, pfx in mount_matches:
        mod = r_var.split('.')[0]
        if mod not in router_mounts:
            router_mounts[mod] = []
        router_mounts[mod].append(pfx.strip() if pfx else "")

    be_routes = []
    be_dir = "backend_app"
    for root, _, files in os.walk(be_dir):
        for f in files:
            if f.endswith('.py'):
                fpath = os.path.join(root, f)
                rel_path = os.path.relpath(fpath).replace("\\", "/")
                with open(fpath, "r", encoding="utf-8", errors="ignore") as src:
                    content = src.read()
                    
                file_base = os.path.splitext(f)[0]
                prefixes = router_mounts.get(file_base, [""])
                
                # Match route endpoints
                matches = re.finditer(r'@(?:router|app)\.(get|post|put|delete|patch|websocket)\s*\(\s*["\']([^"\']+)["\']', content)
                for m in matches:
                    verb = m.group(1).upper()
                    sub_p = m.group(2)
                    for pfx in prefixes:
                        full_p = f"{pfx}{sub_p}" if sub_p.startswith("/") else f"{pfx}/{sub_p}"
                        full_p = re.sub(r'/+', '/', full_p)
                        if full_p.endswith('/') and len(full_p) > 1:
                            full_p = full_p.rstrip('/')
                        be_routes.append({
                            "verb": verb,
                            "full_path": full_p,
                            "file": rel_path,
                            "sub_path": sub_p
                        })

    # 3. FRONTEND API & WEBSOCKET CLIENT AUDIT
    print("\n[3/6] Inspecting all Frontend Pages, Components, Stores, and Callbacks...")
    fe_api_calls = []
    fe_dir = "algo22-terminal/src"
    for root, _, files in os.walk(fe_dir):
        for f in files:
            if f.endswith(('.jsx', '.js', '.tsx', '.ts')):
                fpath = os.path.join(root, f)
                rel_path = os.path.relpath(fpath).replace("\\", "/")
                with open(fpath, "r", encoding="utf-8", errors="ignore") as src:
                    content = src.read()
                    
                api_matches = re.finditer(r'(?:apiClient|client|publicGet|fetch|axios)\.(get|post|put|del|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)[`\'"]', content)
                for am in api_matches:
                    fe_api_calls.append({
                        "file": rel_path,
                        "method": am.group(1).upper(),
                        "endpoint": am.group(2)
                    })

    # 4. ORDER LIFECYCLE & IDEMPOTENCY INSPECTION
    print("\n[4/6] Inspecting Order State Machine & Execution Engine...")
    orders_py = "backend_app/routers/orders.py"
    with open(orders_py, "r", encoding="utf-8") as f:
        orders_content = f.read()
        
    execution_engine_py = "backend_app/core/execution_engine.py"
    with open(execution_engine_py, "r", encoding="utf-8") as f:
        ee_content = f.read()

    # Extract order states
    states = re.findall(r'status\s*=\s*["\']([A-Z_]+)["\']', orders_content + ee_content)
    unique_states = sorted(list(set(states)))

    # Save complete audit facts
    audit_data = {
        "db": {
            "tables_count": len(tables),
            "tables": tables,
            "pks": pks,
            "fks_count": len(fks),
            "fks": fks,
            "uniques_count": len(uniques),
            "uniques": uniques,
            "rls_tables": rls_tables,
            "policies_count": len(policies),
            "policies": [
                {"table": p[0], "name": p[1], "permissive": p[2], "roles": p[3], "cmd": p[4], "qual": p[5]}
                for p in policies
            ],
            "rpcs_count": len(rpcs),
            "rpcs": rpcs
        },
        "be_routes_count": len(be_routes),
        "be_routes": be_routes,
        "fe_api_calls_count": len(fe_api_calls),
        "fe_api_calls": fe_api_calls,
        "order_states": unique_states
    }
    
    os.makedirs("reports", exist_ok=True)
    with open("reports/strict_audit_ground_truth.json", "w", encoding="utf-8") as out:
        json.dump(audit_data, out, indent=2)
        
    print(f"\nAUDIT GROUND TRUTH EXTRACTED:")
    print(f"  Public Tables:       {len(tables)}")
    print(f"  Primary Keys:        {len(pks)}")
    print(f"  Foreign Keys:        {len(fks)}")
    print(f"  Unique Constraints:  {len(uniques)}")
    print(f"  RLS Policies:        {len(policies)}")
    print(f"  RPC Functions:       {len(rpcs)}")
    print(f"  Backend Routes:      {len(be_routes)}")
    print(f"  Frontend API Calls:  {len(fe_api_calls)}")
    print(f"  Extracted Order States: {unique_states}")

if __name__ == '__main__':
    run_strict_audit()
