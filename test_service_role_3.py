import os
import logging
from supabase import create_client, Client

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

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

try:
    response = supabase.table("strategies").select("*").limit(1).execute()
    logger.info(f"Select response: {response}")
    if response.data:
        logger.info(f"Columns: {list(response.data[0].keys())}")
except Exception as e:
    logger.error(f"Error selecting: {e}")
