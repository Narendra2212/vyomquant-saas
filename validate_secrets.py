import os
import requests
import psycopg2
from urllib.parse import urlparse

# Load the env files manually
env_file_1 = r"C:\aerora_quant_backend_updated_final1\claude_audit_package\.env"
env_file_2 = r"C:\aerora_quant_backend_updated_final1\archive\backup_temp\.env.staging"

secrets = {}

for fpath in [env_file_1, env_file_2]:
    if os.path.exists(fpath):
        with open(fpath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    secrets[k.strip()] = v.strip().strip('"').strip("'")

SUPABASE_URL = secrets.get('SUPABASE_URL')
SUPABASE_ANON_KEY = secrets.get('SUPABASE_ANON_KEY')
SUPABASE_SERVICE_ROLE_KEY = secrets.get('SUPABASE_SERVICE_ROLE_KEY')
DATABASE_URL = secrets.get('DATABASE_URL')
SUPABASE_JWT_SECRET = secrets.get('SUPABASE_JWT_SECRET')

print("--- PHASE 1: VALIDATION ---")

# 1. Verify SUPABASE_URL is reachable
try:
    if SUPABASE_URL:
        r = requests.get(f"{SUPABASE_URL}/auth/v1/health", timeout=10)
        print(f"SUPABASE_URL Reachable: {'PASS' if r.status_code == 200 else 'FAIL (' + str(r.status_code) + ')'}")
    else:
        print("SUPABASE_URL: FAIL (Missing)")
except Exception as e:
    print(f"SUPABASE_URL: FAIL ({e})")

# 2. Verify SUPABASE_ANON_KEY
try:
    if SUPABASE_URL and SUPABASE_ANON_KEY:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/", headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {SUPABASE_ANON_KEY}"})
        print(f"SUPABASE_ANON_KEY: {'PASS' if r.status_code == 200 else 'FAIL (' + str(r.status_code) + ')'}")
    else:
        print("SUPABASE_ANON_KEY: FAIL (Missing)")
except Exception as e:
    print(f"SUPABASE_ANON_KEY: FAIL ({e})")

# 3. Verify SUPABASE_SERVICE_ROLE_KEY
try:
    if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/", headers={"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"})
        print(f"SUPABASE_SERVICE_ROLE_KEY: {'PASS' if r.status_code == 200 else 'FAIL (' + str(r.status_code) + ')'}")
    else:
        print("SUPABASE_SERVICE_ROLE_KEY: FAIL (Missing)")
except Exception as e:
    print(f"SUPABASE_SERVICE_ROLE_KEY: FAIL ({e})")

# 4. Verify DATABASE_URL
try:
    if DATABASE_URL:
        # psycopg2 connect
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        conn.close()
        print("DATABASE_URL: PASS")
    else:
        print("DATABASE_URL: FAIL (Missing)")
except Exception as e:
    print(f"DATABASE_URL: FAIL (Exception: {e})")
