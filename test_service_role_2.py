import os
import uuid
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://YOUR_PROJECT_REF.supabase.co")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

try:
    with open(".env", "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("SUPABASE_URL="):
                SUPABASE_URL = line.split("=", 1)[1]
            elif line.startswith("SUPABASE_SERVICE_ROLE_KEY="):
                SUPABASE_SERVICE_ROLE_KEY = line.split("=", 1)[1]
except Exception as e:
    pass

from supabase import create_client, Client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

test_data = {
    "user_id": str(uuid.uuid4()),
    "name": "Service Role Correct Columns",
    "symbol": "BTC/USDT",
    "timeframe": "5m",
    "buy_logic": {},
    "sell_logic": {},
    "risk": {},
    "indicators": [],
    "exchange_id": "binance",
    "status": "stopped",
    "nodes": [],
    "edges": [],
    "dag_version": 1,
    "dag_schema_version": 1,
    "dag_created_at": datetime.now().isoformat(),
    "dag_updated_at": datetime.now().isoformat()
}

try:
    response = supabase.table("strategies").insert(test_data).execute()
    logger.info(f"Insert response: {response}")
except Exception as e:
    logger.error(f"Error inserting: {e}")
