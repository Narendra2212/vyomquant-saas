import os
import json
import logging
import uuid

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
    "id": str(uuid.uuid4()),
    "user_id": str(uuid.uuid4()),
    "name": "Service Role Test",
    "description": "Testing service role",
    "status": "draft",
    "configuration": {"test": "test"}
}

try:
    response = supabase.table("strategies").insert(test_data).execute()
    logger.info(f"Insert response: {response}")
except Exception as e:
    logger.error(f"Error inserting: {e}")
