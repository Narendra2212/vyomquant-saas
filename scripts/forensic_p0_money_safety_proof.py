import os
import re
import sys
import psycopg2
from urllib.parse import urlparse

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")

def audit_p0_invariants():
    print("================================================================================")
    print("FORENSIC P0 MONEY-SAFETY PROOF ENGINE")
    print("================================================================================")
    
    # 1. INVARIANT 6: KILL SWITCH PERSISTENCE & PROCESS RESTART
    print("\n--- [INVARIANT 6] KILL SWITCH PERSISTENCE ---")
    kill_switch_file = "backend_app/core/global_safety.py"
    with open(kill_switch_file, "r", encoding="utf-8") as f:
        ks_content = f.read()
    
    print(f"File: {kill_switch_file}")
    # Inspect storage mechanism
    if "redis" in ks_content.lower():
        print("  Kill switch uses Redis persistence mechanism")
    if "database" in ks_content.lower() or "supabase" in ks_content.lower():
        print("  Kill switch uses Database persistence mechanism")
    if "_is_active" in ks_content or "self.active" in ks_content:
        print("  Kill switch has in-memory state")
        
    # Check default state on boot
    safety_config_file = "backend_app/core/safety_config.py"
    with open(safety_config_file, "r", encoding="utf-8") as f:
        sc_content = f.read()
    if "ExecutionFlags.freeze_all()" in sc_content:
        print("  [SAFE-DEFAULT] safety_config.py freezes all execution flags upon module import")

    # 2. INVARIANT 1 & 13: IDEMPOTENCY & DUPLICATE COLLISION
    print("\n--- [INVARIANT 1 & 13] IDEMPOTENCY & COLLISION FORMULA ---")
    models_er_file = "backend_app/core/models/execution_record.py"
    with open(models_er_file, "r", encoding="utf-8") as f:
        er_content = f.read()
        
    # Find generate_execution_id implementation
    gen_id_match = re.search(r'def generate_execution_id\([^)]+\):.*?(?=\ndef|\Z)', er_content, re.DOTALL)
    if gen_id_match:
        print("generate_execution_id() formula:")
        for line in gen_id_match.group(0).split("\n")[:25]:
            print(f"  {line}")

    # Check database unique constraint for execution_id
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT tc.constraint_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        WHERE tc.table_name = 'execution_records' AND tc.constraint_type = 'UNIQUE';
    """)
    exec_uniques = cur.fetchall()
    print(f"Database Unique Constraints on execution_records: {exec_uniques}")
    
    cur.execute("""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'execution_records'
        ORDER BY ordinal_position;
    """)
    exec_cols = cur.fetchall()
    print("execution_records columns:")
    for c in exec_cols:
        print(f"  {c[0]} ({c[1]}, nullable={c[2]})")

    # 3. INVARIANT 8 & 9: ADVERSARIAL TENANT ISOLATION
    print("\n--- [INVARIANT 8 & 9] ADVERSARIAL TENANT ISOLATION & WS AUTH ---")
    ws_routes_file = "backend_app/api_ws/ws_routes.py"
    with open(ws_routes_file, "r", encoding="utf-8") as f:
        ws_content = f.read()
        
    # Check /ws/user/{user_id} handler
    ws_user_match = re.search(r'@ws_router\.websocket\("/ws/user/\{user_id\}"\).*?(?=\n@|\Z)', ws_content, re.DOTALL)
    if ws_user_match:
        print("Inspection of /ws/user/{user_id} auth check:")
        for line in ws_user_match.group(0).split("\n")[:25]:
            print(f"  {line}")

    # 4. INVARIANT 4 & 5: POSITION RECONCILIATION & ORDER STATE MACHINE
    print("\n--- [INVARIANT 4 & 5] POSITION & RECONCILIATION ---")
    watchdog_file = "backend_app/backend/order_watchdog.py"
    if os.path.exists(watchdog_file):
        with open(watchdog_file, "r", encoding="utf-8") as f:
            wd_content = f.read()
        print(f"Watchdog file exists ({len(wd_content)} bytes)")
        
    reconciler_file = "backend_app/backend/reconciliation_worker.py"
    if os.path.exists(reconciler_file):
        with open(reconciler_file, "r", encoding="utf-8") as f:
            rw_content = f.read()
        print(f"Reconciliation worker file exists ({len(rw_content)} bytes)")

    cur.close()
    conn.close()

if __name__ == '__main__':
    audit_p0_invariants()
