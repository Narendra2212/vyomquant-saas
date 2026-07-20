import os
import uuid
from supabase import create_client

def get_env():
    env_vars = {}
    with open("D:/aerora_quant_backend_updated_final1/.env", "r") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                env_vars[k.strip()] = v.strip().strip('"').strip("'")
    return env_vars

def main():
    env = get_env()
    url = env["SUPABASE_URL"]
    anon_key = env["SUPABASE_ANON_KEY"]
    service_key = env.get("SUPABASE_SERVICE_ROLE_KEY", anon_key)

    admin_client = create_client(url, service_key)

    try:
        # Create users
        email_a = f"tenant_a_{uuid.uuid4().hex[:8]}@example.com"
        email_b = f"tenant_b_{uuid.uuid4().hex[:8]}@example.com"
        
        user_a = admin_client.auth.admin.create_user({"email": email_a, "password": "password123", "email_confirm": True})
        user_b = admin_client.auth.admin.create_user({"email": email_b, "password": "password123", "email_confirm": True})
        
        id_a = user_a.user.id
        id_b = user_b.user.id

        # Insert a strategy for Tenant A using admin client
        strat_id = str(uuid.uuid4())
        admin_client.table("strategies").insert({
            "id": strat_id,
            "user_id": id_a,
            "name": "Tenant A Strategy",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "status": "draft"
        }).execute()
        
        # Now login as Tenant B using anon client
        client_b = create_client(url, anon_key)
        client_b.auth.sign_in_with_password({"email": email_b, "password": "password123"})
        
        # Attempt read
        read_res = client_b.table("strategies").select("*").eq("id", strat_id).execute()
        
        if len(read_res.data) == 0:
            print("RLS PASS")
        else:
            print("RLS FAIL")

    except Exception as e:
        print(f"RLS ERROR: {e}")

if __name__ == "__main__":
    main()
