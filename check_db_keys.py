import os
import sys

# Set up paths
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'aerora_quant_backend_updated_final1')))
import asyncio
from core.database import SessionLocal
from core.models.pydantic_models import ExchangeResponse
# Actually we can just query the raw DB table for exchange credentials
from sqlalchemy import text

async def check_db():
    db = SessionLocal()
    try:
        # Check users table for tenant ID
        result = db.execute(text("SELECT id, email FROM users WHERE email = 'your-test-email@example.com'"))
        user = result.fetchone()
        if not user:
            print("User not found.")
            return
            
        tenant_id = user[0]
        print(f"Tenant ID: {tenant_id}")
        
        # Query exchange connections
        result = db.execute(text(f"SELECT exchange_id, api_key_preview, is_active, is_testnet FROM exchange_connections WHERE tenant_id = '{tenant_id}'"))
        connections = result.fetchall()
        print(f"Connections found: {len(connections)}")
        for conn in connections:
            print(conn)
            
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(check_db())
