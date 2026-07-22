import asyncio
import os
import traceback

import httpx
from dotenv import load_dotenv

from supabase import create_client

load_dotenv('d:/aerora_quant_backend_updated_final1/backend_app/.env')

supabase_url = os.environ.get('SUPABASE_URL')
supabase_key = os.environ.get('SUPABASE_ANON_KEY')
supabase = create_client(supabase_url, supabase_key)

async def fetch_endpoint(client, url, headers):
    try:
        print(f"\n--- GET {url} ---")
        resp = await client.get(url, headers=headers, timeout=15.0)
        print("Status:", resp.status_code)
        print("Body:", resp.json() if resp.status_code == 200 else resp.text)
    except Exception as e:
        print("Error fetching:", str(e))

async def test():
    try:
        res = supabase.auth.sign_in_with_password({'email': 'narendra@aeroradynamics.in', 'password': os.environ.get('TEST_USER_PASSWORD', 'YOUR_TEST_PASSWORD')})
        token = res.session.access_token
        
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {token}"}
            await fetch_endpoint(client, "http://127.0.0.1:8000/api/analytics/performance?days=30", headers)
            await fetch_endpoint(client, "http://127.0.0.1:8000/api/portfolio/heatmap?months=3", headers)

    except Exception:
        traceback.print_exc()

asyncio.run(test())
