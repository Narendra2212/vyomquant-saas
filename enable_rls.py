import os
import json
import httpx
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://YOUR_PROJECT_REF.supabase.co")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# Load keys from .env
try:
    with open(".env", "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("SUPABASE_URL="):
                SUPABASE_URL = line.split("=", 1)[1]
            elif line.startswith("SUPABASE_ANON_KEY="):
                SUPABASE_ANON_KEY = line.split("=", 1)[1]
            elif line.startswith("SUPABASE_SERVICE_ROLE_KEY="):
                SUPABASE_SERVICE_ROLE_KEY = line.split("=", 1)[1]
except Exception as e:
    logger.error(f"Failed to read .env: {e}")

from supabase import create_client, Client

logger.info(f"Using Supabase URL: {SUPABASE_URL}")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

try:
    response = supabase.rpc("rls_auto_enable", {}).execute()
    logger.info(f"rls_auto_enable response: {response}")
except Exception as e:
    logger.error(f"Error calling rls_auto_enable: {e}")
