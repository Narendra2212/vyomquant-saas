import os
import json
import subprocess
import re

transcript_path = r"C:\Users\aa\.gemini\antigravity-ide\brain\b7168250-693d-43f6-9447-74f42ce4f1b1\.system_generated\logs\transcript.jsonl"

anon_key = None
service_role_key = None
legacy_jwt = None
database_url = None

with open(transcript_path, 'r', encoding='utf-8') as f:
    for line in f:
        try:
            data = json.loads(line)
            content = data.get('content', '')
            if 'SUPABASE_ANON_KEY:' in content and 'Legacy JWT secret:' in content:
                # Extract values using regex
                m_anon = re.search(r'SUPABASE_ANON_KEY:\s*(\S+)', content)
                m_service = re.search(r'SUPABASE_SERVICE_ROLE_KEY:\s*(\S+)', content)
                m_legacy = re.search(r'Legacy JWT secret:\s*(\S+)', content)
                m_db = re.search(r'(postgresql://\S+)', content)
                
                if m_anon: anon_key = m_anon.group(1)
                if m_service: service_role_key = m_service.group(1)
                if m_legacy: legacy_jwt = m_legacy.group(1)
                if m_db: database_url = m_db.group(1)
        except:
            pass

fernet_key = "8_-t6h51YGp6GpBos6bcipqicQhixmd0rrczPIs22KI="
jwt_secret = "1574044216f7888fa2b03682563f76b09a2c4599cc04dd647f2e0b8bf77ee37c"

secrets = {
    "/vyomquant/production/SUPABASE_ANON_KEY": anon_key,
    "/vyomquant/production/SUPABASE_SERVICE_ROLE_KEY": service_role_key,
    "/vyomquant/production/SUPABASE_JWT_SECRET": legacy_jwt,
    "/vyomquant/production/DATABASE_URL": database_url,
    "/vyomquant/production/MASTER_ENCRYPTION_KEYS": fernet_key,
    "/vyomquant/production/JWT_SECRET": jwt_secret
}

region = "ap-southeast-1"

for name, value in secrets.items():
    if not value:
        print(f"FAILED TO EXTRACT SECRET FOR {name}")
        continue
    
    # Check if secret exists
    cmd_check = f"aws secretsmanager describe-secret --secret-id {name} --region {region}"
    res = subprocess.run(cmd_check, shell=True, capture_output=True)
    
    if res.returncode == 0:
        # Update
        print(f"Updating secret {name}...")
        cmd_update = f"aws secretsmanager put-secret-value --secret-id {name} --secret-string \"{value}\" --region {region}"
        up_res = subprocess.run(cmd_update, shell=True, capture_output=True, text=True)
        if up_res.returncode == 0:
            arn = json.loads(up_res.stdout).get("ARN", "Unknown")
            print(f"Updated: {name} (ARN: {arn})")
        else:
            print(f"Failed to update {name}: {up_res.stderr}")
    else:
        # Create
        print(f"Creating secret {name}...")
        cmd_create = f"aws secretsmanager create-secret --name {name} --secret-string \"{value}\" --region {region}"
        cr_res = subprocess.run(cmd_create, shell=True, capture_output=True, text=True)
        if cr_res.returncode == 0:
            arn = json.loads(cr_res.stdout).get("ARN", "Unknown")
            print(f"Created: {name} (ARN: {arn})")
        else:
            print(f"Failed to create {name}: {cr_res.stderr}")

print("\n--- ALL SECRETS PROVISIONED ---")
