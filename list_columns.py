import os
import httpx
import asyncio
import json

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://YOUR_PROJECT_REF.supabase.co")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

if not SUPABASE_SERVICE_ROLE_KEY:
    try:
        with open(".env", "r") as f:
            for line in f:
                if line.startswith("SUPABASE_SERVICE_ROLE_KEY="):
                    SUPABASE_SERVICE_ROLE_KEY = line.split("=", 1)[1].strip()
    except:
        pass

async def main():
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{SUPABASE_URL}/rest/v1/", headers=headers)
        if resp.status_code == 200:
            openapi = resp.json()
            defs = openapi.get("definitions", {})
            for table in ["profiles", "processed_orders", "strategies"]:
                props = defs.get(table, {}).get("properties", {})
                print(f"Columns for {table}:")
                for c, info in props.items():
                    print(f"  {c}: {info.get('type')}")
        else:
            print("Failed:", resp.status_code, resp.text)

if __name__ == "__main__":
    asyncio.run(main())
