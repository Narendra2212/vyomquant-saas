import asyncio
import os
import sys

from supabase import create_client, Client

SUPABASE_URL = "https://YOUR_PROJECT_REF.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "YOUR_SUPABASE_SERVICE_ROLE_KEY"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

async def check():
    # fetch users
    response = supabase.table('users').select('*').eq('email', 'your-test-email@example.com').execute()
    users = response.data
    if not users:
        print("User not found")
        return
    
    tenant_id = users[0]['id']
    print(f"Tenant ID: {tenant_id}")
    
    # fetch exchange connections
    connections = supabase.table('exchange_connections').select('*').eq('tenant_id', tenant_id).execute()
    print(f"Connections: {connections.data}")
    
if __name__ == "__main__":
    asyncio.run(check())
