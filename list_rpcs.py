import os
import httpx
import asyncio

SUPABASE_URL = "https://YOUR_PROJECT_REF.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "YOUR_SUPABASE_SERVICE_ROLE_KEY"

async def main():
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{SUPABASE_URL}/rest/v1/", headers=headers)
        if resp.status_code == 200:
            openapi = resp.json()
            paths = openapi.get("paths", {})
            print("RPC paths found:")
            for p in paths.keys():
                if p.startswith("/rpc/"):
                    print(f"  {p}")
        else:
            print("Failed:", resp.status_code, resp.text)

if __name__ == "__main__":
    asyncio.run(main())
